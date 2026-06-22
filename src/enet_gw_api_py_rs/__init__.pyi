"""Type stubs for the eNet gateway Python wrapper."""

from typing import AsyncIterator, List, Optional

from .discovery import GatewayInfo as GatewayInfo, discover_gateways as discover_gateways

__all__ = [
    "EnetClient",
    "Device",
    "DeviceValue",
    "DeviceStream",
    "discover_gateways",
    "GatewayInfo",
]

class DeviceValue:
    """A device's state/value at a point in time."""

    @property
    def is_on(self) -> bool:
        """Whether the device is currently on."""

    @property
    def brightness(self) -> Optional[int]:
        """Brightness in percent (0..=100) for dimmers, else ``None``."""

    @property
    def state(self) -> str:
        """Lowercase textual state, e.g. ``"on"``, ``"off"``, ``"undefined"``."""

    @property
    def is_undefined(self) -> bool:
        """``True`` while the gateway has not yet reported a state."""

    def __eq__(self, other: object) -> bool: ...
    def __repr__(self) -> str: ...

class DeviceStream:
    """Async iterator yielding :class:`DeviceValue` updates for a device."""

    def __aiter__(self) -> DeviceStream: ...
    async def __anext__(self) -> DeviceValue: ...

class Device:
    """A single controllable device exposed by the gateway."""

    @property
    def number(self) -> int:
        """The gateway's numeric identifier for this device."""

    @property
    def name(self) -> str:
        """The human-readable name from the eNet project."""

    @property
    def kind(self) -> str:
        """The device kind: ``"binary"``, ``"dimmer"`` or ``"blinds"``."""

    def subscribe(self) -> DeviceStream:
        """Return an async iterator of state updates for this device."""

    def __repr__(self) -> str: ...

class EnetClient:
    """A connected client for an eNet gateway."""

    @staticmethod
    async def connect(host: str, port: int = 5000) -> EnetClient:
        """Connect to the gateway at ``host``:``port`` and fetch its project."""

    @property
    def devices(self) -> List[Device]:
        """All controllable devices reported by the gateway."""

    def device(self, number: int) -> Optional[Device]:
        """Look up a device by its number, or ``None``."""

    async def turn_on(self, number: int, long: bool = False) -> None:
        """Turn a device on (``long`` sends a long click)."""

    async def turn_off(self, number: int, long: bool = False) -> None:
        """Turn a device off (``long`` sends a long click)."""

    async def set_brightness(self, number: int, brightness: int) -> None:
        """Set a dimmer's brightness to a percentage (0..=100)."""

    async def set_blinds_position(self, number: int, position: int) -> None:
        """Move a blinds device to a position percentage (0..=100)."""

    def __repr__(self) -> str: ...
