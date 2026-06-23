"""Python wrapper for the Jung/Gira "Funk Gateway IP" (eNet) client.

This package wraps the Rust `enet-client` / `enet-proto` crates and exposes an
asyncio-friendly API intended for use in a Home Assistant integration.
"""

from .enet_gw_api_py_rs import (
    Device,
    DeviceStream,
    DeviceValue,
    EnetClient,
    enable_logging,
)
from .discovery import GatewayInfo, discover_gateways

__all__ = [
    "EnetClient",
    "Device",
    "DeviceValue",
    "DeviceStream",
    "discover_gateways",
    "GatewayInfo",
    "enable_logging",
]
