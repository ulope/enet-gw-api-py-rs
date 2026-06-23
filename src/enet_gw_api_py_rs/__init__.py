"""Python wrapper for the Jung/Gira "Funk Gateway IP" (eNet) client.

This package wraps the Rust `enet-client` / `enet-proto` crates and exposes an
asyncio-friendly API intended for use in a Home Assistant integration.
"""

import logging

from .enet_gw_api_py_rs import (
    Device,
    DeviceStream,
    DeviceValue,
    EnetClient,
    reset_logging_cache,
)
from .discovery import GatewayInfo, discover_gateways

# Follow the library convention of not configuring logging ourselves: attach a
# no-op handler so importing the package never emits "No handlers" warnings, and
# let the application decide how logs (including the bridged Rust logs) surface.
logging.getLogger(__name__).addHandler(logging.NullHandler())

__all__ = [
    "EnetClient",
    "Device",
    "DeviceValue",
    "DeviceStream",
    "discover_gateways",
    "GatewayInfo",
    "reset_logging_cache",
]
