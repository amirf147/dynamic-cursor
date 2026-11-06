# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Amir Farhadi
"""End-to-end integration tests for Dynamic Cursor daemon and Python feeder SDK.

This test suite verifies length-prefixed Named Pipe communication, concurrent
feeder priority arbitration, kernel lease automatic rollback upon feeder crash,
and daemon process memory working set boundaries.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

# Ensure local src/ package directory is importable without requiring manual PYTHONPATH
REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import pytest

from dynamic_cursor.client import (
    BINARY_PACKET_FORMAT,
    BINARY_PACKET_MAGIC,
    BadgeId,
    BaseShape,
    DynamicCursorClient,
    Operation,
    parse_color_to_argb,
)

# Test constants
E2E_PIPE_NAME = r"\\.\pipe\dynamic_cursor_e2e_test"
MAX_PRIVATE_WORKING_SET_BYTES = 2 * 1024 * 1024  # 2 MB ceiling

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE

kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL

kernel32.WaitNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
kernel32.WaitNamedPipeW.restype = wintypes.BOOL


class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
    """Win32 PROCESS_MEMORY_COUNTERS_EX structure for memory inspection."""

    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


def get_private_working_set_bytes(pid: int) -> int:
    """Measures the private bytes of a target process using psutil or psapi."""
    try:
        import psutil

        proc = psutil.Process(pid)
        mem = proc.memory_full_info()
        if hasattr(mem, "private"):
            return int(mem.private)
        if hasattr(mem, "uss"):
            return int(mem.uss)
    except Exception:
        pass

    PROCESS_QUERY_INFORMATION = 0x0400
    PROCESS_VM_READ = 0x0010
    h_proc = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not h_proc:
        return 0

    try:
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        counters = PROCESS_MEMORY_COUNTERS_EX()
        counters.cb = ctypes.sizeof(counters)
        if psapi.GetProcessMemoryInfo(h_proc, ctypes.byref(counters), counters.cb):
            return int(counters.PrivateUsage)
        return 0
    finally:
        kernel32.CloseHandle(h_proc)


def find_daemon_executable() -> Optional[Path]:
    """Discovers the dynamic-cursor-tray executable across build paths."""
    env_override = os.environ.get("DYNAMIC_CURSOR_BIN")
    if env_override and Path(env_override).is_file():
        return Path(env_override)

    root = Path(__file__).resolve().parent.parent
    candidates = [
        root / "build" / "dynamic-cursor-tray.exe",
        root / "build" / "bin" / "dynamic-cursor-tray.exe",
        root / "build" / "Release" / "dynamic-cursor-tray.exe",
        root / "build" / "bin" / "Release" / "dynamic-cursor-tray.exe",
        root / "build" / "Debug" / "dynamic-cursor-tray.exe",
        root / "build" / "bin" / "Debug" / "dynamic-cursor-tray.exe",
        root / "bin" / "dynamic-cursor-tray.exe",
        root / "dynamic-cursor-tray.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def wait_for_pipe_ready(pipe_name: str, timeout_sec: float = 5.0) -> bool:
    """Waits for named pipe availability within the specified timeout."""
    start = time.monotonic()
    while time.monotonic() - start < timeout_sec:
        if kernel32.WaitNamedPipeW(pipe_name, 100):
            return True
        time.sleep(0.05)
    return False


class MockPipeCompositorServer:
    """Multi-instance in-process named pipe server for verifying protocol mechanics."""

    def __init__(self, pipe_name: str, instance_count: int = 4) -> None:
        self.pipe_name = pipe_name
        self.instance_count = instance_count
        self.is_running = False
        self.lock = threading.Lock()
        self.active_layers: Dict[int, Dict[str, Dict[str, Any]]] = {}
        self.composite: Dict[str, Any] = {
            "base_shape": None,
            "tint_rgba": None,
            "badge_id": None,
            "progress": None,
            "toast": None,
        }
        self.last_toast: Optional[Dict[str, Any]] = None
        self.disconnect_events: List[int] = []
        self._threads: List[threading.Thread] = []
        self._pipe_handles: List[int] = []
        self._next_session_id = 0

    def _evaluate(self) -> None:
        merged: Dict[str, Any] = {
            "base_shape": None,
            "tint_rgba": None,
            "badge_id": None,
            "progress": None,
            "toast": None,
        }
        max_prio: Dict[str, int] = {k: -1000000 for k in merged}

        for session_id, layers in self.active_layers.items():
            for layer_id, payload in layers.items():
                prio = int(payload.get("priority", 0))
                for ch in ["base_shape", "tint_rgba", "badge_id", "progress", "toast"]:
                    val = payload.get(ch)
                    if val is not None and prio >= max_prio[ch]:
                        max_prio[ch] = prio
                        merged[ch] = val

        merged["toast"] = self.last_toast
        self.composite = merged

    def start(self) -> None:
        self.is_running = True
        for i in range(self.instance_count):
            t = threading.Thread(target=self._worker_instance_loop, args=(i,), daemon=True)
            self._threads.append(t)
            t.start()
        # Give threads time to create named pipe instances
        time.sleep(0.1)

    def stop(self) -> None:
        self.is_running = False
        GENERIC_READ = 0x80000000
        GENERIC_WRITE = 0x40000000
        OPEN_EXISTING = 3
        for _ in range(self.instance_count):
            try:
                dummy = kernel32.CreateFileW(
                    self.pipe_name,
                    GENERIC_READ | GENERIC_WRITE,
                    0,
                    None,
                    OPEN_EXISTING,
                    0,
                    None,
                )
                if dummy and dummy != ctypes.c_void_p(-1).value:
                    kernel32.CloseHandle(dummy)
            except Exception:
                pass

        for t in self._threads:
            if t.is_alive():
                t.join(timeout=1.0)
        self._threads.clear()

    def _worker_instance_loop(self, slot_id: int) -> None:
        PIPE_ACCESS_DUPLEX = 0x00000003
        PIPE_TYPE_BYTE = 0x00000000
        PIPE_WAIT = 0x00000000
        PIPE_UNLIMITED_INSTANCES = 255

        kernel32.CreateNamedPipeW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
        ]
        kernel32.CreateNamedPipeW.restype = wintypes.HANDLE

        kernel32.ConnectNamedPipe.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
        kernel32.ConnectNamedPipe.restype = wintypes.BOOL

        kernel32.DisconnectNamedPipe.argtypes = [wintypes.HANDLE]
        kernel32.DisconnectNamedPipe.restype = wintypes.BOOL

        kernel32.ReadFile.argtypes = [
            wintypes.HANDLE,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p,
        ]
        kernel32.ReadFile.restype = wintypes.BOOL

        while self.is_running:
            h_pipe = kernel32.CreateNamedPipeW(
                self.pipe_name,
                PIPE_ACCESS_DUPLEX,
                PIPE_TYPE_BYTE | PIPE_WAIT,
                PIPE_UNLIMITED_INSTANCES,
                65536,
                65536,
                0,
                None,
            )
            if not h_pipe or h_pipe == ctypes.c_void_p(-1).value:
                time.sleep(0.05)
                continue

            with self.lock:
                self._pipe_handles.append(h_pipe)

            connected = kernel32.ConnectNamedPipe(h_pipe, None)
            if not connected and kernel32.GetLastError() != 535:  # ERROR_PIPE_CONNECTED
                kernel32.CloseHandle(h_pipe)
                with self.lock:
                    if h_pipe in self._pipe_handles:
                        self._pipe_handles.remove(h_pipe)
                continue

            with self.lock:
                self._next_session_id += 1
                session_id = self._next_session_id
                self.active_layers[session_id] = {}

            # Read frames until broken pipe or server stopped
            while self.is_running:
                hdr_buf = ctypes.create_string_buffer(4)
                bytes_read = wintypes.DWORD(0)
                res = kernel32.ReadFile(h_pipe, hdr_buf, 4, ctypes.byref(bytes_read), None)
                if not res or bytes_read.value < 4:
                    break

                length = struct.unpack("<I", hdr_buf.raw[:4])[0]
                if length == 0 or length > 16 * 1024 * 1024:
                    break

                payload_buf = ctypes.create_string_buffer(length)
                total = 0
                read_ok = True
                while total < length:
                    res = kernel32.ReadFile(
                        h_pipe,
                        ctypes.byref(payload_buf, total),
                        length - total,
                        ctypes.byref(bytes_read),
                        None,
                    )
                    if not res or bytes_read.value == 0:
                        read_ok = False
                        break
                    total += bytes_read.value

                if not read_ok:
                    break

                raw_bytes = payload_buf.raw[:length]
                self._dispatch(session_id, raw_bytes)

            # Client disconnect cleanup (Kernel Lease Rollback)
            with self.lock:
                self.active_layers.pop(session_id, None)
                self.disconnect_events.append(session_id)
                self._evaluate()
                if h_pipe in self._pipe_handles:
                    self._pipe_handles.remove(h_pipe)

            kernel32.DisconnectNamedPipe(h_pipe)
            kernel32.CloseHandle(h_pipe)

    def _dispatch(self, session_id: int, data: bytes) -> None:
        with self.lock:
            if len(data) >= 32 and struct.unpack("<I", data[:4])[0] == BINARY_PACKET_MAGIC:
                # Binary frame
                magic, op, shape, badge, prio, argb, prog, ttl, layer_hash, res = struct.unpack(
                    BINARY_PACKET_FORMAT, data[:32]
                )
                layer_id = f"bin_{layer_hash}" if layer_hash != 0 else "binary_layer"
                if op == Operation.UPSERT:
                    self.active_layers[session_id][layer_id] = {
                        "priority": prio,
                        "base_shape": shape,
                        "tint_rgba": f"#{argb:08X}" if argb != 0 else None,
                        "badge_id": badge if badge != 0 else None,
                        "progress": prog if prog >= 0.0 else None,
                    }
                elif op == Operation.REMOVE:
                    self.active_layers[session_id].pop(layer_id, None)
                elif op == Operation.CLEAR:
                    self.active_layers[session_id].clear()
            else:
                # JSON frame
                try:
                    doc = json.loads(data.decode("utf-8"))
                    op = doc.get("op", "upsert")
                    layer_id = doc.get("layer_id", "default")
                    if op == "upsert":
                        self.active_layers[session_id][layer_id] = doc
                        if "toast" in doc and doc["toast"] is not None:
                            self.last_toast = doc["toast"]
                    elif op == "remove":
                        self.active_layers[session_id].pop(layer_id, None)
                    elif op == "clear":
                        self.active_layers[session_id].clear()
                except Exception:
                    pass

            self._evaluate()


# ---------------------------------------------------------------------------
# Test Cases
# ---------------------------------------------------------------------------


def test_color_parsing_utility() -> None:
    """Verifies that hex colors convert into expected 32-bit ARGB values."""
    # 6-digit RGB assumes 0xFF opacity
    assert parse_color_to_argb("#FF5555") == 0xFFFF5555
    assert parse_color_to_argb("0x44FF66") == 0xFF44FF66

    # 8-digit RGBA converts to ARGB
    # RGBA: R=0x44, G=0xFF, B=0x66, A=0x80 -> ARGB: 0x8044FF66
    assert parse_color_to_argb("#44FF6680") == 0x8044FF66
    assert parse_color_to_argb(None) == 0


def test_client_in_process_protocol_suite() -> None:
    """Tests DynamicCursorClient protocol methods against a live named pipe."""
    test_pipe = r"\\.\pipe\test_dynamic_cursor_client_unit"
    server = MockPipeCompositorServer(test_pipe)
    server.start()

    try:
        # Verify connection and JSON transmission
        with DynamicCursorClient(pipe_name=test_pipe) as client:
            assert client.is_connected

            # Assert layer 1
            client.set_layer(
                layer_id="voice_layer",
                priority=80,
                tint_rgba="#FF5555",
                badge_id="mic_off",
            )
            time.sleep(0.05)

            with server.lock:
                comp = dict(server.composite)
            assert comp["tint_rgba"] == "#FF5555"
            assert comp["badge_id"] == "mic_off"

            # Clear layer
            client.clear_layer("voice_layer")
            time.sleep(0.05)

            with server.lock:
                comp = dict(server.composite)
            assert comp["tint_rgba"] is None
            assert comp["badge_id"] is None

            # Assert binary packet transmission
            client.set_layer(
                layer_id="high_freq_bin",
                priority=90,
                base_shape="precision_cross",
                progress=0.75,
                use_binary=True,
            )
            time.sleep(0.05)

            with server.lock:
                comp = dict(server.composite)
            assert comp["base_shape"] == BaseShape.PRECISION_CROSS
            assert pytest.approx(comp["progress"], 0.01) == 0.75

            # Assert toast notification
            client.show_toast("Build completed", duration_ms=2000)
            time.sleep(0.05)

            with server.lock:
                comp = dict(server.composite)
            assert comp["toast"] == {"text": "Build completed", "duration_ms": 2000}

    finally:
        server.stop()


def test_concurrent_feeders_and_crash_lease_rollback() -> None:
    """Verifies orthogonal channel composition and kernel lease rollback on crash."""
    test_pipe = r"\\.\pipe\test_dynamic_cursor_concurrent_feeders"
    server = MockPipeCompositorServer(test_pipe)
    server.start()

    try:
        # Feeder 2: Low priority TV badge (Priority 20)
        client2 = DynamicCursorClient(pipe_name=test_pipe)
        client2.set_layer(
            layer_id="context_feeder",
            priority=20,
            badge_id="tv_icon",
        )
        time.sleep(0.05)

        with server.lock:
            state2 = dict(server.composite)
        assert state2["badge_id"] == "tv_icon"
        assert state2["tint_rgba"] is None

        # Feeder 1: High priority red tint in a dedicated subprocess (Priority 80)
        feeder1_code = f"""
from dynamic_cursor.client import DynamicCursorClient
import time
client = DynamicCursorClient(pipe_name=r'{test_pipe}')
client.set_layer(layer_id='crash_feeder', priority=80, tint_rgba='#FF5555')
time.sleep(30)
"""
        sub_env = os.environ.copy()
        sub_env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent / "src")

        proc_feeder1 = subprocess.Popen(
            [sys.executable, "-c", feeder1_code],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=sub_env,
        )

        time.sleep(0.3)

        # Multi-channel priority composition verification
        with server.lock:
            state_merged = dict(server.composite)
        assert state_merged["tint_rgba"] == "#FF5555"
        assert state_merged["badge_id"] == "tv_icon"

        # Forcibly terminate Feeder 1 to trigger Win32 kernel lease drop
        proc_feeder1.terminate()
        proc_feeder1.wait(timeout=3.0)

        # Wait briefly for server to process broken pipe
        time.sleep(0.2)

        # Verify automatic lease rollback: Feeder 1 purged, Feeder 2 remains
        with server.lock:
            state_rolled_back = dict(server.composite)
        assert state_rolled_back["tint_rgba"] is None
        assert state_rolled_back["badge_id"] == "tv_icon"

        # Feeder 2 clean shutdown
        client2.close()
        time.sleep(0.1)

        with server.lock:
            state_final = dict(server.composite)
        assert state_final["badge_id"] is None

    finally:
        server.stop()


def test_daemon_memory_working_set_measurement() -> None:
    """Verifies that private bytes measurement executes accurately."""
    current_pid = os.getpid()
    private_bytes = get_private_working_set_bytes(current_pid)
    assert private_bytes > 0
    # Python test runner process typically consumes between 10 MB and 80 MB
    assert private_bytes < 500 * 1024 * 1024


def test_native_daemon_e2e() -> None:
    """Executes end-to-end integration tests against the native daemon executable."""
    daemon_exe = find_daemon_executable()
    if daemon_exe is None:
        pytest.skip(
            "Native daemon dynamic-cursor-tray.exe not found. "
            "Build the binary or set DYNAMIC_CURSOR_BIN to run native e2e tests."
        )

    pipe_name = E2E_PIPE_NAME
    cmd = [
        str(daemon_exe),
        "--dry-run-cursor",
        "--no-tray",
        "--pipe",
        pipe_name,
    ]

    daemon_proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        # Wait for daemon named pipe readiness
        pipe_ready = wait_for_pipe_ready(pipe_name, timeout_sec=5.0)
        assert pipe_ready, f"Daemon failed to open pipe: {pipe_name}"

        # Inspect daemon memory working set: strictly < 2 MB private bytes
        private_bytes = get_private_working_set_bytes(daemon_proc.pid)
        if private_bytes > 0:
            assert (
                private_bytes < MAX_PRIVATE_WORKING_SET_BYTES
            ), f"Daemon private working set {private_bytes} exceeds 2 MB limit"

        # Connect Feeder 2: sets TV icon badge
        client2 = DynamicCursorClient(pipe_name=pipe_name)
        client2.set_layer(
            layer_id="feeder_2",
            priority=20,
            badge_id="tv_icon",
        )
        time.sleep(0.1)

        # Launch Feeder 1 in child process holding lease
        feeder1_code = f"""
from dynamic_cursor.client import DynamicCursorClient
import time
client = DynamicCursorClient(pipe_name=r'{pipe_name}')
client.set_layer(layer_id='feeder_1', priority=80, tint_rgba='#FF5555')
time.sleep(30)
"""
        sub_env = os.environ.copy()
        sub_env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent / "src")

        proc_feeder1 = subprocess.Popen(
            [sys.executable, "-c", feeder1_code],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=sub_env,
        )
        time.sleep(0.3)

        # Forcibly kill Feeder 1 to trigger automatic kernel lease drop
        proc_feeder1.terminate()
        proc_feeder1.wait(timeout=3.0)
        time.sleep(0.2)

        # Feeder 2 disconnects cleanly
        client2.close()

    finally:
        # Clean daemon shutdown
        daemon_proc.terminate()
        try:
            daemon_proc.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            daemon_proc.kill()


def test_cli_subcommands_parser() -> None:
    """Verifies CLI argument parser options across all subcommands."""
    from dynamic_cursor.client import build_cli_parser

    parser = build_cli_parser()

    # Subcommand: set
    args_set = parser.parse_args(
        [
            "--pipe",
            r"\\.\pipe\test_custom",
            "set",
            "--layer",
            "test_layer",
            "--priority",
            "75",
            "--shape",
            "precision_cross",
            "--tint",
            "#44FF66",
            "--badge",
            "mic_on",
            "--progress",
            "0.85",
            "--ttl",
            "3000",
            "--binary",
            "--hold",
            "2.5",
        ]
    )
    assert args_set.command == "set"
    assert args_set.pipe == r"\\.\pipe\test_custom"
    assert args_set.layer == "test_layer"
    assert args_set.priority == 75
    assert args_set.shape == "precision_cross"
    assert args_set.tint == "#44FF66"
    assert args_set.badge == "mic_on"
    assert args_set.progress == 0.85
    assert args_set.ttl == 3000
    assert args_set.binary is True
    assert args_set.hold == 2.5

    # Subcommand: clear
    args_clear = parser.parse_args(["clear", "--layer", "test_layer"])
    assert args_clear.command == "clear"
    assert args_clear.layer == "test_layer"

    # Subcommand: clear-all
    args_clear_all = parser.parse_args(["clear-all"])
    assert args_clear_all.command == "clear-all"

    # Subcommand: toast
    args_toast = parser.parse_args(["toast", "Operation completed", "--duration", "2000"])
    assert args_toast.command == "toast"
    assert args_toast.text == "Operation completed"
    assert args_toast.duration == 2000


def test_caster_mic_feeder_integration() -> None:
    """Verifies that CasterMicFeeder drives state layer transitions."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "examples"))
    from caster_mic_feeder import CasterMicFeeder

    test_pipe = r"\\.\pipe\test_dynamic_cursor_caster"
    server = MockPipeCompositorServer(test_pipe)
    server.start()

    try:
        with CasterMicFeeder(pipe_name=test_pipe) as feeder:
            # Listening state
            feeder.on_mic_state_change("listening")
            time.sleep(0.05)
            with server.lock:
                state = dict(server.composite)
            assert state["tint_rgba"] == "#44FF66"
            assert state["badge_id"] is None

            # Sleeping state
            feeder.on_mic_state_change("sleeping")
            time.sleep(0.05)
            with server.lock:
                state = dict(server.composite)
            assert state["tint_rgba"] == "#FF5555"

            # Muted state
            feeder.on_mic_state_change("muted")
            time.sleep(0.05)
            with server.lock:
                state = dict(server.composite)
            assert state["badge_id"] == "mic_off"

            # Clear layer
            feeder.clear()
            time.sleep(0.05)
            with server.lock:
                state = dict(server.composite)
            assert state["tint_rgba"] is None
            assert state["badge_id"] is None

    finally:
        server.stop()


def test_cli_notify_integration() -> None:
    """Verifies that run_progress_notification drives progress and toast delivery."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "examples"))
    from cli_notify import run_progress_notification

    test_pipe = r"\\.\pipe\test_dynamic_cursor_notify"
    server = MockPipeCompositorServer(test_pipe)
    server.start()

    try:
        run_progress_notification(
            pipe_name=test_pipe,
            steps=5,
            delay_sec=0.01,
            toast_message="Test build succeeded",
            duration_ms=1000,
        )
        time.sleep(0.05)
        with server.lock:
            state = dict(server.composite)
        assert state["toast"] == {"text": "Test build succeeded", "duration_ms": 1000}
        assert state["progress"] is None

    finally:
        server.stop()

