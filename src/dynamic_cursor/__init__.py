# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Amir Farhadi
"""Dynamic Cursor feeder client package.

This package provides an event-driven Python client for communicating with
the Dynamic Cursor Windows daemon over local Win32 named pipes.
"""

from typing import Any

__version__ = "0.1.0"

__all__ = [
    "BadgeId",
    "BaseShape",
    "DynamicCursorClient",
    "Operation",
    "__version__",
]


def __getattr__(name: str) -> Any:
    """Resolves exported attributes lazily to prevent module preloading warnings."""
    if name in {"BadgeId", "BaseShape", "DynamicCursorClient", "Operation"}:
        from . import client

        return getattr(client, name)
    raise AttributeError(f"module {repr(__name__)} has no attribute {repr(name)}")
