# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Amir Farhadi
"""Dynamic Cursor Win32 Named Pipe client.

This module provides the Python feeder SDK for communicating with the
Dynamic Cursor daemon over local Win32 named pipes using length-prefixed framing.
"""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from enum import IntEnum
import json
import struct
import sys
import time
from typing import Any, Dict, List, Optional, Union

# Win32 Named Pipe constants
DEFAULT_PIPE_NAME = r"\\.\pipe\dynamic_cursor"
BINARY_PACKET_MAGIC = 0x44435552
BINARY_PACKET_FORMAT = "<IBBHiIfIIB3s"
BINARY_PACKET_SIZE = 32
FRAME_HEADER_SIZE = 4
MAX_PAYLOAD_SIZE = 16 * 1024 * 1024

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3
FILE_ATTRIBUTE_NORMAL = 0x80

ERROR_FILE_NOT_FOUND = 2
ERROR_BROKEN_PIPE = 109
ERROR_PIPE_BUSY = 231
ERROR_PIPE_NOT_CONNECTED = 233

INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

# Setup 64-bit safe Win32 API prototypes
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

kernel32.CreateFileW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.DWORD,
    wintypes.DWORD,
    ctypes.c_void_p,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.HANDLE,
]
kernel32.CreateFileW.restype = wintypes.HANDLE

kernel32.WriteFile.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    ctypes.c_void_p,
]
kernel32.WriteFile.restype = wintypes.BOOL

kernel32.FlushFileBuffers.argtypes = [wintypes.HANDLE]
kernel32.FlushFileBuffers.restype = wintypes.BOOL

kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL

kernel32.GetLastError.argtypes = []
kernel32.GetLastError.restype = wintypes.DWORD

kernel32.WaitNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
kernel32.WaitNamedPipeW.restype = wintypes.BOOL


class BaseShape(IntEnum):
    """Base cursor shapes supported by the hardware plane."""

    DEFAULT_ARROW = 0
    PRECISION_CROSS = 1
    READING_MINIMAL = 2
    TEXT_IBEAM = 3
    HAND_POINT = 4


class BadgeId(IntEnum):
    """Corner badge sprite identifiers rendered in the lower-right quadrant."""

    NONE = 0
    MIC_OFF = 1
    MIC_ON = 2
    TV_ICON = 3
    VIM_NORMAL = 4
    WEB_GLOBE = 5
    CODE_BRACKETS = 6
    TERMINAL_PROMPT = 7
    DOC_WRITING = 8


class ProgressStyle(IntEnum):
    """Progress presentation styles."""

    MINIMAL = 0
    OVERLAY = 1
    BOTH = 2


class Operation(IntEnum):
    """Protocol operations for layer mutations."""

    UPSERT = 1
    REMOVE = 2
    CLEAR = 3


def parse_color_to_argb(color: Union[str, int, None]) -> int:
    """Converts hexadecimal strings or numeric color values to 32-bit ARGB format."""
    if color is None:
        return 0

    if isinstance(color, int):
        if color <= 0x00FFFFFF:
            return 0xFF000000 | color
        return color & 0xFFFFFFFF

    raw_str = str(color).strip()
    if raw_str.startswith("#"):
        raw_str = raw_str[1:]
    elif raw_str.startswith("0x") or raw_str.startswith("0X"):
        raw_str = raw_str[2:]

    try:
        val = int(raw_str, 16)
    except ValueError:
        return 0

    if len(raw_str) <= 6:
        rgb = val & 0x00FFFFFF
        return 0xFF000000 | rgb

    if len(raw_str) == 8:
        # Input interpreted as RGBA format
        r = (val >> 24) & 0xFF
        g = (val >> 16) & 0xFF
        b = (val >> 8) & 0xFF
        a = val & 0xFF
        return (a << 24) | (r << 16) | (g << 8) | b

    return val & 0xFFFFFFFF


def normalize_shape(shape: Union[str, int, BaseShape, None]) -> int:
    """Maps shape names or enum members to numeric identifiers."""
    if shape is None:
        return 0
    if isinstance(shape, BaseShape):
        return int(shape.value)
    if isinstance(shape, int):
        return shape if 0 <= shape <= 4 else 0

    name = str(shape).strip().lower()
    mapping = {
        "default_arrow": 0,
        "arrow": 0,
        "default": 0,
        "0": 0,
        "precision_cross": 1,
        "cross": 1,
        "1": 1,
        "reading_minimal": 2,
        "reading": 2,
        "minimal": 2,
        "2": 2,
        "text_ibeam": 3,
        "ibeam": 3,
        "text": 3,
        "3": 3,
        "hand_point": 4,
        "hand": 4,
        "link": 4,
        "4": 4,
    }
    return mapping.get(name, 0)


def normalize_badge(badge: Union[str, int, BadgeId, None]) -> int:
    """Maps badge names or enum members to numeric identifiers."""
    if badge is None:
        return 0
    if isinstance(badge, BadgeId):
        return int(badge.value)
    if isinstance(badge, int):
        return badge if 0 <= badge <= 8 else 0

    name = str(badge).strip().lower()
    mapping = {
        "none": 0,
        "0": 0,
        "": 0,
        "mic_off": 1,
        "mic_muted": 1,
        "muted": 1,
        "1": 1,
        "mic_on": 2,
        "listening": 2,
        "2": 2,
        "tv_icon": 3,
        "tv": 3,
        "3": 3,
        "vim_normal": 4,
        "vim": 4,
        "4": 4,
        "web_globe": 5,
        "web": 5,
        "browser": 5,
        "globe": 5,
        "5": 5,
        "code_brackets": 6,
        "code": 6,
        "ide": 6,
        "editor": 6,
        "6": 6,
        "terminal_prompt": 7,
        "terminal": 7,
        "shell": 7,
        "console": 7,
        "7": 7,
        "doc_writing": 8,
        "writing": 8,
        "document": 8,
        "doc": 8,
        "prose": 8,
        "8": 8,
    }
    return mapping.get(name, 0)


def normalize_progress_style(style: Union[str, int, ProgressStyle, None]) -> int:
    """Maps progress style names or enum members to numeric identifiers."""
    if style is None:
        return 0
    if isinstance(style, ProgressStyle):
        return int(style.value)
    if isinstance(style, int):
        return style if 0 <= style <= 2 else 0

    name = str(style).strip().lower()
    mapping = {
        "minimal": 0,
        "in_cursor": 0,
        "cursor": 0,
        "0": 0,
        "": 0,
        "overlay": 1,
        "companion": 1,
        "hud": 1,
        "card": 1,
        "1": 1,
        "both": 2,
        "all": 2,
        "2": 2,
    }
    return mapping.get(name, 0)


def compute_layer_hash(layer_id: str) -> int:
    """Computes a 32-bit FNV-1a hash representing a layer identifier."""
    hash_val = 2166136261
    for byte in layer_id.encode("utf-8"):
        hash_val ^= byte
        hash_val = (hash_val * 16777619) & 0xFFFFFFFF
    return hash_val


class DynamicCursorClient:
    """Win32 Named Pipe client managing visual state layer mutations.

    This client connects to the Dynamic Cursor daemon and transmits
    length-prefixed JSON or packed 32-byte binary frames.
    """

    def __init__(
        self,
        pipe_name: str = DEFAULT_PIPE_NAME,
        timeout_ms: int = 5000,
        auto_connect: bool = True,
    ) -> None:
        self._pipe_name = pipe_name
        self._timeout_ms = timeout_ms
        self._handle: Optional[int] = None

        if auto_connect:
            self.connect()

    @property
    def is_connected(self) -> bool:
        """Indicates whether the named pipe handle is currently open."""
        return self._handle is not None and self._handle != INVALID_HANDLE_VALUE

    @property
    def pipe_name(self) -> str:
        """Returns the configured named pipe path."""
        return self._pipe_name

    def connect(self, timeout_ms: Optional[int] = None) -> None:
        """Connects to the Win32 Named Pipe server."""
        if self.is_connected:
            return

        limit_ms = self._timeout_ms if timeout_ms is None else timeout_ms
        start_time = time.monotonic()

        while True:
            handle = kernel32.CreateFileW(
                self._pipe_name,
                GENERIC_READ | GENERIC_WRITE,
                0,
                None,
                OPEN_EXISTING,
                0,
                None,
            )

            if handle != INVALID_HANDLE_VALUE and handle is not None:
                self._handle = handle
                return

            err = kernel32.GetLastError()
            elapsed_ms = (time.monotonic() - start_time) * 1000.0

            if err == ERROR_PIPE_BUSY:
                remaining_ms = max(0, int(limit_ms - elapsed_ms))
                if remaining_ms <= 0:
                    raise TimeoutError(
                        f"Timed out waiting for pipe '{self._pipe_name}'"
                    )
                wait_slice = min(remaining_ms, 200)
                kernel32.WaitNamedPipeW(self._pipe_name, wait_slice)
            elif err == ERROR_FILE_NOT_FOUND:
                if elapsed_ms >= limit_ms:
                    raise FileNotFoundError(
                        f"Named pipe not found: '{self._pipe_name}' (error {err})"
                    )
                time.sleep(0.05)
            else:
                raise ConnectionError(
                    f"Failed to open named pipe '{self._pipe_name}'. Win32 error code: {err}"
                )

    def close(self) -> None:
        """Closes the active named pipe handle."""
        if self._handle is not None and self._handle != INVALID_HANDLE_VALUE:
            kernel32.CloseHandle(self._handle)
            self._handle = None

    def __enter__(self) -> DynamicCursorClient:
        if not self.is_connected:
            self.connect()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def _write_frame(self, payload_bytes: bytes) -> None:
        """Writes a 4-byte length-prefixed frame to the named pipe."""
        if not self.is_connected:
            self.connect()

        frame = struct.pack("<I", len(payload_bytes)) + payload_bytes
        total_written = 0

        while total_written < len(frame):
            to_write = len(frame) - total_written
            chunk = frame[total_written:]
            written = wintypes.DWORD(0)

            res = kernel32.WriteFile(
                self._handle,
                chunk,
                to_write,
                ctypes.byref(written),
                None,
            )

            if not res:
                err = kernel32.GetLastError()
                self.close()
                raise ConnectionError(
                    f"Failed to write frame to pipe. Win32 error code: {err}"
                )

            total_written += written.value

        kernel32.FlushFileBuffers(self._handle)

    def send_json(self, payload: Dict[str, Any]) -> None:
        """Encodes a dictionary as UTF-8 JSON and transmits a framed packet."""
        json_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        if len(json_bytes) > MAX_PAYLOAD_SIZE:
            raise ValueError(f"Payload size {len(json_bytes)} exceeds maximum limit")
        self._write_frame(json_bytes)

    def send_binary(
        self,
        op: int,
        priority: int,
        shape: int = 0,
        badge: int = 0,
        tint_argb: int = 0,
        progress: float = -1.0,
        ttl_ms: int = 0,
        layer_hash: int = 0,
        progress_style: int = 0,
    ) -> None:
        """Packs attributes into a 32-byte binary packet and transmits a framed packet."""
        packet = struct.pack(
            BINARY_PACKET_FORMAT,
            BINARY_PACKET_MAGIC,
            int(op) & 0xFF,
            int(shape) & 0xFF,
            int(badge) & 0xFFFF,
            int(priority),
            int(tint_argb) & 0xFFFFFFFF,
            float(progress),
            int(ttl_ms) & 0xFFFFFFFF,
            int(layer_hash) & 0xFFFFFFFF,
            int(progress_style) & 0xFF,
            b"\x00\x00\x00",
        )
        self._write_frame(packet)

    def set_layer(
        self,
        layer_id: str,
        priority: int,
        base_shape: Optional[str] = None,
        tint_rgba: Optional[str] = None,
        badge_id: Optional[str] = None,
        progress: Optional[float] = None,
        progress_style: Optional[str] = None,
        ttl_ms: int = 0,
        use_binary: bool = False,
    ) -> None:
        """Asserts or updates a visual cursor state layer."""
        if use_binary:
            shape_val = normalize_shape(base_shape)
            badge_val = normalize_badge(badge_id)
            argb_val = parse_color_to_argb(tint_rgba)
            prog_val = float(progress) if progress is not None else -1.0
            style_val = normalize_progress_style(progress_style)
            layer_hash = compute_layer_hash(layer_id)
            self.send_binary(
                op=Operation.UPSERT,
                priority=priority,
                shape=shape_val,
                badge=badge_val,
                tint_argb=argb_val,
                progress=prog_val,
                ttl_ms=ttl_ms,
                layer_hash=layer_hash,
                progress_style=style_val,
            )
            return

        payload: Dict[str, Any] = {
            "op": "upsert",
            "layer_id": layer_id,
            "priority": priority,
        }

        if base_shape is not None:
            payload["base_shape"] = base_shape
        if tint_rgba is not None:
            payload["tint_rgba"] = tint_rgba
        if badge_id is not None:
            payload["badge_id"] = badge_id
        if progress is not None:
            payload["progress"] = progress
        if progress_style is not None:
            payload["progress_style"] = progress_style
        if ttl_ms > 0:
            payload["ttl_ms"] = ttl_ms

        self.send_json(payload)

    def clear_layer(self, layer_id: str) -> None:
        """Removes a previously asserted visual layer."""
        payload = {
            "op": "remove",
            "layer_id": layer_id,
        }
        self.send_json(payload)

    def clear_all(self) -> None:
        """Purges all active layers associated with this client connection."""
        payload = {
            "op": "clear",
        }
        self.send_json(payload)

    def show_toast(
        self,
        text: str,
        duration_ms: int = 1500,
        follow_cursor: bool = True,
    ) -> None:
        """Dispatches an ephemeral micro-notification to the companion overlay."""
        payload = {
            "op": "upsert",
            "layer_id": "toast_overlay",
            "priority": 1000,
            "ttl_ms": duration_ms,
            "toast": {
                "text": text,
                "duration_ms": duration_ms,
                "follow_cursor": follow_cursor,
            },
        }
        self.send_json(payload)


def build_cli_parser() -> argparse.ArgumentParser:
    """Constructs the command line interface parser."""
    parser = argparse.ArgumentParser(
        prog="python -m dynamic_cursor.client",
        description="Dynamic Cursor command line client utility.",
    )
    parser.add_argument(
        "--pipe",
        type=str,
        default=DEFAULT_PIPE_NAME,
        help="Named pipe path (default: \\\\.\\pipe\\dynamic_cursor)",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # Subcommand: set
    set_parser = subparsers.add_parser("set", help="Assert or update a visual layer.")
    set_parser.add_argument(
        "--layer",
        type=str,
        default="default",
        help="Unique identifier for the layer.",
    )
    set_parser.add_argument(
        "--priority",
        type=int,
        default=50,
        help="Arbitration priority integer.",
    )
    set_parser.add_argument(
        "--shape",
        type=str,
        default=None,
        choices=["default_arrow", "precision_cross", "reading_minimal"],
        help="Base cursor shape identifier.",
    )
    set_parser.add_argument(
        "--tint",
        type=str,
        default=None,
        help="Pointer tint color in hex format (e.g. #FF5555 or #44FF6680).",
    )
    set_parser.add_argument(
        "--badge",
        type=str,
        default=None,
        choices=[
            "none",
            "mic_off",
            "mic_on",
            "tv_icon",
            "vim_normal",
            "web_globe",
            "code_brackets",
            "terminal_prompt",
            "doc_writing",
        ],
        help="Corner badge sprite identifier.",
    )
    set_parser.add_argument(
        "--progress",
        type=float,
        default=None,
        help="Progress ratio normalized between 0.0 and 1.0.",
    )
    set_parser.add_argument(
        "--ttl",
        type=int,
        default=0,
        help="Time-to-live duration in milliseconds.",
    )
    set_parser.add_argument(
        "--binary",
        action="store_true",
        default=False,
        help="Transmit payload using packed 32-byte binary format.",
    )
    set_parser.add_argument(
        "--hold",
        type=float,
        default=0.0,
        help="Duration in seconds to hold pipe lease before exiting.",
    )

    # Subcommand: clear
    clear_parser = subparsers.add_parser("clear", help="Remove an asserted layer.")
    clear_parser.add_argument(
        "--layer",
        type=str,
        required=True,
        help="Unique identifier of the layer to remove.",
    )

    # Subcommand: clear-all
    subparsers.add_parser(
        "clear-all",
        help="Purge all active layers for this connection.",
    )

    # Subcommand: toast
    toast_parser = subparsers.add_parser(
        "toast",
        help="Display an ephemeral micro-notification.",
    )
    toast_parser.add_argument(
        "text",
        type=str,
        help="Notification message text.",
    )
    toast_parser.add_argument(
        "--duration",
        type=int,
        default=1500,
        help="Toast visibility duration in milliseconds.",
    )

    return parser


def main(args: Optional[List[str]] = None) -> int:
    """Executes the client CLI dispatch routine."""
    parser = build_cli_parser()
    parsed = parser.parse_args(args)

    try:
        client = DynamicCursorClient(pipe_name=parsed.pipe)
    except Exception as exc:
        sys.stderr.write(f"Connection failed: {exc}\n")
        return 1

    try:
        with client:
            if parsed.command == "set":
                client.set_layer(
                    layer_id=parsed.layer,
                    priority=parsed.priority,
                    base_shape=parsed.shape,
                    tint_rgba=parsed.tint,
                    badge_id=parsed.badge,
                    progress=parsed.progress,
                    ttl_ms=parsed.ttl,
                    use_binary=parsed.binary,
                )
                if parsed.hold > 0.0:
                    time.sleep(parsed.hold)
            elif parsed.command == "clear":
                client.clear_layer(parsed.layer)
            elif parsed.command == "clear-all":
                client.clear_all()
            elif parsed.command == "toast":
                client.show_toast(parsed.text, duration_ms=parsed.duration)
        return 0
    except Exception as exc:
        sys.stderr.write(f"Operation failed: {exc}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
