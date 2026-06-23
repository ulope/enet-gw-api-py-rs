"""Pure-Python autodiscovery of eNet Funk Gateways on the local network.

eNet gateways answer a UDP broadcast "ping" on the local subnet. This module
sends that broadcast and collects the replies. The protocol matches the
ioBroker.enet implementation (https://github.com/stoffel7/ioBroker.enet):

* the client broadcasts a fixed magic payload to UDP port 3112,
* gateways reply (to the sender) with a packet carrying the magic word
  ``0xFEAF``, the gateway's IP, hostname and MAC address.
"""

from __future__ import annotations

import asyncio
import logging
import socket
from dataclasses import dataclass
from typing import Dict, List, Optional

__all__ = ["GatewayInfo", "discover_gateways"]

_LOGGER = logging.getLogger(__name__)

# The magic "knock" every eNet gateway listens for.
DISCOVERY_PAYLOAD = b"Ich wusste, dass Sie zurueck kommen wuerden...\x00\x02"
# Port the gateways listen on for the broadcast.
DISCOVERY_BROADCAST_PORT = 3112
# Port the gateways send their replies to.
DISCOVERY_LISTEN_PORT = 2906
# 16-bit magic word (little-endian) at offset 1 of every reply.
DISCOVERY_MAGIC = 0xFEAF


@dataclass(frozen=True)
class GatewayInfo:
    """A gateway discovered on the network."""

    host: str
    """The gateway's IP address (pass this to ``EnetClient.connect``)."""
    name: str
    """The gateway's hostname."""
    mac: str
    """The gateway's MAC address, lower-case and colon-separated."""
    device_state: int
    """A raw status byte reported by the gateway."""
    manufacturer: int
    """A raw manufacturer/vendor byte reported by the gateway."""


def _parse_reply(msg: bytes) -> Optional[GatewayInfo]:
    """Parse a discovery reply, or return ``None`` if it isn't a valid one."""
    if len(msg) < 15:
        return None
    if int.from_bytes(msg[1:3], "little") != DISCOVERY_MAGIC:
        return None

    host = ".".join(str(b) for b in msg[3:7])
    name = msg[7:-9].split(b"\x00", 1)[0].decode("latin-1", errors="replace")
    mac = ":".join(f"{b:02x}" for b in msg[-8:-2])
    return GatewayInfo(
        host=host,
        name=name,
        mac=mac,
        device_state=msg[-2],
        manufacturer=msg[-1],
    )


class _DiscoveryProtocol(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.gateways: Dict[str, GatewayInfo] = {}

    def datagram_received(self, data: bytes, addr) -> None:
        info = _parse_reply(data)
        if info is None:
            _LOGGER.debug("ignoring non-gateway packet from %s (%d bytes)", addr, len(data))
            return
        if info.mac not in self.gateways:
            _LOGGER.info("discovered gateway %s (%s) at %s", info.name, info.mac, info.host)
        # De-duplicate by MAC; the same gateway answers every broadcast.
        self.gateways[info.mac] = info


async def discover_gateways(
    timeout: float = 5.0,
    *,
    broadcast_address: str = "255.255.255.255",
    broadcast_port: int = DISCOVERY_BROADCAST_PORT,
    listen_port: int = DISCOVERY_LISTEN_PORT,
    attempts: int = 3,
) -> List[GatewayInfo]:
    """Discover eNet gateways on the local network.

    Broadcasts the discovery payload ``attempts`` times, spread evenly over
    ``timeout`` seconds, and returns every gateway that replied (de-duplicated by
    MAC address).

    Args:
        timeout: Total time to spend discovering, in seconds.
        broadcast_address: Where to broadcast. Use a subnet-directed broadcast
            (e.g. ``"192.168.1.255"``) if global broadcast is filtered.
        broadcast_port: UDP port the gateways listen on (default 3112).
        listen_port: Local UDP port to bind for replies (default 2906, matching
            the reference implementation). Pass ``0`` for an ephemeral port.
        attempts: How many times to send the broadcast.

    Returns:
        A list of :class:`GatewayInfo`, possibly empty.
    """
    loop = asyncio.get_running_loop()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.setblocking(False)
    sock.bind(("", listen_port))

    transport, protocol = await loop.create_datagram_endpoint(
        _DiscoveryProtocol, sock=sock
    )
    assert isinstance(protocol, _DiscoveryProtocol)

    _LOGGER.debug(
        "starting gateway discovery: broadcast %s:%d, %d attempt(s) over %.1fs",
        broadcast_address, broadcast_port, attempts, timeout,
    )
    try:
        interval = timeout / attempts if attempts > 0 else timeout
        for i in range(max(attempts, 1)):
            try:
                transport.sendto(
                    DISCOVERY_PAYLOAD, (broadcast_address, broadcast_port)
                )
                _LOGGER.debug("sent discovery broadcast %d/%d", i + 1, attempts)
            except OSError as exc:
                # Network momentarily unavailable; keep trying / waiting.
                _LOGGER.debug("discovery broadcast failed: %s", exc)
            # Sleep after each send (including the last) so late replies land.
            await asyncio.sleep(interval)
    finally:
        transport.close()

    gateways = list(protocol.gateways.values())
    _LOGGER.debug("discovery finished: %d gateway(s) found", len(gateways))
    return gateways
