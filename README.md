# enet-gw-api-py-rs

Python bindings for the Jung/Gira **Funk Gateway IP** (eNet), built on top of
the Rust [`enet-client`](https://crates.io/crates/enet-client) and
[`enet-proto`](https://crates.io/crates/enet-proto) crates via
[PyO3](https://pyo3.rs) + [maturin](https://www.maturin.rs).

The API is fully **asyncio-based** so it can be dropped into a Home Assistant
integration: every method that talks to the gateway returns an awaitable, and
device state updates are exposed as async iterators. Network IO runs on a
background Tokio runtime, so it never blocks the Python event loop.

## Installation

```bash
pip install enet-gw-api-py-rs
```

Or, for local development:

```bash
maturin develop --uv
```

## Usage

```python
import asyncio
from enet_gw_api_py_rs import EnetClient


async def main():
    # `port` defaults to 5000 (the standard Funk Gateway IP port).
    client = await EnetClient.connect("192.168.1.50")

    # Enumerate the devices the gateway knows about.
    for device in client.devices:
        print(device.number, device.name, device.kind)
        # kind is one of: "binary", "dimmer", "blinds"

    # Send commands (by device number).
    await client.turn_on(1)
    await client.turn_off(1)
    await client.set_brightness(2, 50)   # dimmers, 0..=100

    # Subscribe to live state updates for a device.
    async def watch(device):
        async for value in device.subscribe():
            print(device.name, "->", value.state,
                  "on" if value.is_on else "off", value.brightness)

    await asyncio.gather(*(watch(d) for d in client.devices))


asyncio.run(main())
```

### API surface

| Object | Member | Description |
| ------ | ------ | ----------- |
| `EnetClient` | `await connect(host, port=5000)` | Connect and fetch the project. |
| | `devices -> list[Device]` | All controllable devices. |
| | `device(number) -> Device | None` | Look up a device by number. |
| | `await turn_on(number, long=False)` | Turn a device on. |
| | `await turn_off(number, long=False)` | Turn a device off. |
| | `await set_brightness(number, pct)` | Set a dimmer to `0..=100`. |
| `Device` | `number`, `name`, `kind` | Device metadata. |
| | `subscribe() -> DeviceStream` | Async iterator of updates. |
| `DeviceValue` | `is_on`, `brightness`, `state`, `is_undefined` | A point-in-time value. |
| `DeviceStream` | `async for value in ...` | Live state updates. |

### Home Assistant notes

The subscription is a plain async iterator, which maps cleanly onto a Home
Assistant entity:

```python
async def _listen(self):
    async for value in self._device.subscribe():
        self._attr_is_on = value.is_on
        if value.brightness is not None:
            self._attr_brightness = round(value.brightness * 255 / 100)
        self.async_write_ha_state()
```

Start `_listen` as a background task in `async_added_to_hass` and cancel it in
`async_will_remove_from_hass`.

## Limitations

These come from the underlying `enet-client` 0.2.1 crate:

- **Blinds (Jalousie) devices are not fully supported.** The crate panics when
  building/commanding a blinds device, so connecting to a project that contains
  one will raise. Only binary (switch) and dimmer devices are usable today.
- Device commands use the device **number**, while the gateway internally keys
  live updates by channel index; this wrapper hides that distinction — you only
  ever deal with device numbers.

## Development

```bash
maturin develop --uv        # build + install into the active venv
pytest src/tests            # run the (mock-gateway) test suite
```

The test suite starts an in-process asyncio TCP server that speaks the eNet
protocol, so it exercises the full connect / subscribe / command path without a
real gateway.
