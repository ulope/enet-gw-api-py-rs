"""Tests for the eNet gateway Python wrapper.

The integration tests spin up a small asyncio TCP server that speaks the eNet
"Funk Gateway IP" protocol (newline-delimited JSON) closely enough to drive the
real Rust client through a full connect / subscribe / command cycle.
"""

import asyncio
import json

import pytest

import enet_gw_api_py_rs
from enet_gw_api_py_rs import EnetClient, Device, DeviceValue, DeviceStream


DELIMITER = b"\r\n\r\n"


class MockGateway:
    """A minimal in-process eNet gateway for testing.

    It answers the handshake commands (version, channel info, project list),
    handles sign-in on the event connection, records ``ITEM_VALUE_SET`` commands
    and can push ``ITEM_UPDATE_IND`` messages to subscribers.
    """

    # index -> project item. Index position matters: it lines up with the
    # GET_CHANNEL_INFO_ALL "DEVICES" array.
    ITEMS = [
        {"TYPE": "BINAER", "NUMBER": 1, "NAME": "Light", "PROGRAMMABLE": True},
        {"TYPE": "DIMMER", "NUMBER": 2, "NAME": "Dimmer"},
    ]
    # Both items are real devices (value == 1).
    CHANNEL_DEVICES = [1, 1]

    def __init__(self):
        self.server = None
        self.host = "127.0.0.1"
        self.port = None
        self.set_commands = []
        # Event-connection writers that have signed in, used to push updates.
        self._event_writers = []
        self._signed_in = asyncio.Event()

    async def start(self):
        self.server = await asyncio.start_server(self._handle, self.host, 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def stop(self):
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()

    @staticmethod
    def _frame(payload: dict) -> bytes:
        body = {"PROTOCOL": "0.03", **payload}
        return json.dumps(body).encode() + DELIMITER

    async def _handle(self, reader, writer):
        buffer = b""
        try:
            while True:
                chunk = await reader.read(4096)
                if not chunk:
                    break
                buffer += chunk
                while DELIMITER in buffer:
                    raw, buffer = buffer.split(DELIMITER, 1)
                    if raw.strip():
                        await self._dispatch(json.loads(raw), writer)
        except (ConnectionResetError, asyncio.CancelledError):
            pass

    async def _dispatch(self, msg, writer):
        cmd = msg.get("CMD")
        if cmd == "VERSION_REQ":
            writer.write(self._frame({
                "CMD": "VERSION_RES",
                "FIRMWARE": "1.0", "HARDWARE": "1.0", "ENET": "1.0",
            }))
        elif cmd == "GET_CHANNEL_INFO_ALL_REQ":
            writer.write(self._frame({
                "CMD": "GET_CHANNEL_INFO_ALL_RES",
                "DEVICES": self.CHANNEL_DEVICES,
            }))
        elif cmd == "PROJECT_LIST_GET":
            writer.write(self._frame({
                "CMD": "PROJECT_LIST_RES",
                "PROJECT_ID": "test",
                "ITEMS": self.ITEMS,
                "LISTS": [{
                    "NUMBER": 0, "NAME": "Room",
                    "ITEMS_ORDER": [1, 2], "VISIBLE": True,
                }],
            }))
        elif cmd == "ITEM_VALUE_SIGN_IN_REQ":
            writer.write(self._frame({"CMD": "ITEM_VALUE_SIGN_IN_RES"}))
            self._event_writers.append(writer)
            self._signed_in.set()
        elif cmd == "ITEM_VALUE_SET":
            self.set_commands.append(msg)
            writer.write(self._frame({"CMD": "ITEM_VALUE_RES"}))
        await writer.drain()

    async def push_update(self, index, state, value="0", setpoint="0"):
        """Push an ITEM_UPDATE_IND to every signed-in event connection.

        Note: sign-in and updates are keyed by the *channel index* (the position
        in the project item list), not the device's project number. Index 0 maps
        to device number 1, index 1 to device number 2, and so on.
        """
        await self._signed_in.wait()
        frame = self._frame({
            "CMD": "ITEM_UPDATE_IND",
            "VALUES": [{
                "NUMBER": str(index), "VALUE": str(value),
                "STATE": state, "SETPOINT": str(setpoint),
            }],
        })
        for writer in self._event_writers:
            writer.write(frame)
            await writer.drain()


def test_module_exposes_public_api():
    assert hasattr(enet_gw_api_py_rs, "EnetClient")
    assert hasattr(enet_gw_api_py_rs, "Device")
    assert hasattr(enet_gw_api_py_rs, "DeviceValue")
    assert hasattr(enet_gw_api_py_rs, "DeviceStream")


def test_set_brightness_rejects_out_of_range():
    async def scenario():
        gw = MockGateway()
        await gw.start()
        try:
            client = await EnetClient.connect(gw.host, gw.port)
            with pytest.raises(ValueError):
                await client.set_brightness(2, 150)
        finally:
            await gw.stop()

    asyncio.run(scenario())


def test_connect_and_list_devices():
    async def scenario():
        gw = MockGateway()
        await gw.start()
        try:
            client = await EnetClient.connect(gw.host, gw.port)
            devices = client.devices
            assert {d.number for d in devices} == {1, 2}
            light = client.device(1)
            assert isinstance(light, Device)
            assert light.name == "Light"
            assert light.kind == "binary"
            dimmer = client.device(2)
            assert dimmer.kind == "dimmer"
            assert client.device(999) is None
        finally:
            await gw.stop()

    asyncio.run(scenario())


def test_subscribe_receives_updates():
    async def scenario():
        gw = MockGateway()
        await gw.start()
        try:
            client = await EnetClient.connect(gw.host, gw.port)
            stream = client.device(2).subscribe()
            assert isinstance(stream, DeviceStream)
            it = stream.__aiter__()

            # Push an "ON at 50%" update for device 2 (channel index 1) and read
            # it back off the stream.
            await gw.push_update(1, "ON", value="50")
            value = await asyncio.wait_for(it.__anext__(), timeout=5)
            assert isinstance(value, DeviceValue)
            assert value.is_on is True
            assert value.brightness == 50
        finally:
            await gw.stop()

    asyncio.run(scenario())


def test_turn_on_sends_command():
    async def scenario():
        gw = MockGateway()
        await gw.start()
        try:
            client = await EnetClient.connect(gw.host, gw.port)
            await client.turn_on(1)
            # Give the command actor a moment to forward the request.
            for _ in range(50):
                if gw.set_commands:
                    break
                await asyncio.sleep(0.05)
            assert gw.set_commands, "gateway never received a set command"
            sent = gw.set_commands[-1]
            assert sent["CMD"] == "ITEM_VALUE_SET"
            values = sent["VALUES"]
            assert values[0]["NUMBER"] == 1
            assert values[0]["STATE"] == "ON"
        finally:
            await gw.stop()

    asyncio.run(scenario())
