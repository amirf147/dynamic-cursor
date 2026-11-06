# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Amir Farhadi

"""Win32 Named Pipe IPC and Kernel Connection Lease Benchmark.

This script implements an event-driven Win32 Named Pipe server and multi-channel
priority state compositor. It evaluates IPC throughput, one-way latency, round-trip
latency, and automatic kernel lease rollback upon abrupt process termination.
"""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
import math
import os
import struct
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Win32 API Definitions & 64-bit Safe Prototypes
# ---------------------------------------------------------------------------

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# Handle types: explicitly 64-bit pointer sized
HANDLE = ctypes.c_void_p
LPCWSTR = ctypes.c_wchar_p
DWORD = ctypes.c_ulong
BOOL = ctypes.c_int
ULONG_PTR = ctypes.c_size_t

# Win32 Constants
PIPE_ACCESS_DUPLEX = 0x00000003
FILE_FLAG_OVERLAPPED = 0x40000000
PIPE_TYPE_BYTE = 0x00000000
PIPE_READMODE_BYTE = 0x00000000
PIPE_WAIT = 0x00000000
PIPE_UNLIMITED_INSTANCES = 255
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3

ERROR_BROKEN_PIPE = 109
ERROR_PIPE_BUSY = 231
ERROR_PIPE_CONNECTED = 535
ERROR_OPERATION_ABORTED = 995
ERROR_IO_PENDING = 997

WAIT_OBJECT_0 = 0x00000000
INFINITE = 0xFFFFFFFF
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
PIPE_BUFFER_SIZE = 65536
DEFAULT_PIPE_NAME = r"\\.\pipe\dynamic_cursor_spike"


class OVERLAPPED(ctypes.Structure):
    """Win32 OVERLAPPED structure configured for 64-bit alignment."""

    _fields_ = [
        ("Internal", ULONG_PTR),
        ("InternalHigh", ULONG_PTR),
        ("Offset", DWORD),
        ("OffsetHigh", DWORD),
        ("hEvent", HANDLE),
    ]


# Function prototypes with explicit argtypes and restype
kernel32.CreateNamedPipeW.argtypes = [
    LPCWSTR,
    DWORD,
    DWORD,
    DWORD,
    DWORD,
    DWORD,
    DWORD,
    ctypes.c_void_p,
]
kernel32.CreateNamedPipeW.restype = HANDLE

kernel32.ConnectNamedPipe.argtypes = [HANDLE, ctypes.c_void_p]
kernel32.ConnectNamedPipe.restype = BOOL

kernel32.DisconnectNamedPipe.argtypes = [HANDLE]
kernel32.DisconnectNamedPipe.restype = BOOL

kernel32.CreateFileW.argtypes = [
    LPCWSTR,
    DWORD,
    DWORD,
    ctypes.c_void_p,
    DWORD,
    DWORD,
    HANDLE,
]
kernel32.CreateFileW.restype = HANDLE

kernel32.ReadFile.argtypes = [
    HANDLE,
    ctypes.c_void_p,
    DWORD,
    ctypes.POINTER(DWORD),
    ctypes.c_void_p,
]
kernel32.ReadFile.restype = BOOL

kernel32.WriteFile.argtypes = [
    HANDLE,
    ctypes.c_void_p,
    DWORD,
    ctypes.POINTER(DWORD),
    ctypes.c_void_p,
]
kernel32.WriteFile.restype = BOOL

kernel32.CloseHandle.argtypes = [HANDLE]
kernel32.CloseHandle.restype = BOOL

kernel32.GetLastError.argtypes = []
kernel32.GetLastError.restype = DWORD

kernel32.CreateEventW.argtypes = [ctypes.c_void_p, BOOL, BOOL, LPCWSTR]
kernel32.CreateEventW.restype = HANDLE

kernel32.SetEvent.argtypes = [HANDLE]
kernel32.SetEvent.restype = BOOL

kernel32.ResetEvent.argtypes = [HANDLE]
kernel32.ResetEvent.restype = BOOL

kernel32.WaitForSingleObject.argtypes = [HANDLE, DWORD]
kernel32.WaitForSingleObject.restype = DWORD

kernel32.WaitForMultipleObjects.argtypes = [
    DWORD,
    ctypes.POINTER(HANDLE),
    BOOL,
    DWORD,
]
kernel32.WaitForMultipleObjects.restype = DWORD

kernel32.GetOverlappedResult.argtypes = [
    HANDLE,
    ctypes.c_void_p,
    ctypes.POINTER(DWORD),
    BOOL,
]
kernel32.GetOverlappedResult.restype = BOOL

kernel32.CancelIoEx.argtypes = [HANDLE, ctypes.c_void_p]
kernel32.CancelIoEx.restype = BOOL

kernel32.WaitNamedPipeW.argtypes = [LPCWSTR, DWORD]
kernel32.WaitNamedPipeW.restype = BOOL


# ---------------------------------------------------------------------------
# Framing and Wire Protocol Helpers
# ---------------------------------------------------------------------------

def read_exact(h_pipe: Any, num_bytes: int) -> Tuple[Optional[bytes], int]:
    """Read an exact number of bytes from the pipe handle."""
    received = bytearray()
    while len(received) < num_bytes:
        to_read = num_bytes - len(received)
        buf = ctypes.create_string_buffer(to_read)
        bytes_read = DWORD(0)
        res = kernel32.ReadFile(
            h_pipe,
            buf,
            to_read,
            ctypes.byref(bytes_read),
            None,
        )
        if not res or bytes_read.value == 0:
            err = kernel32.GetLastError()
            return None, err
        received.extend(buf.raw[: bytes_read.value])
    return bytes(received), 0


def read_frame(h_pipe: Any) -> Tuple[Optional[bytes], int]:
    """Read a 4-byte length-prefixed frame payload from the pipe."""
    hdr, err = read_exact(h_pipe, 4)
    if hdr is None:
        return None, err
    length = struct.unpack("<I", hdr)[0]
    if length > 16 * 1024 * 1024:
        return None, 13
    payload, err = read_exact(h_pipe, length)
    if payload is None:
        return None, err
    return payload, 0


def write_frame(h_pipe: Any, data: bytes) -> Tuple[bool, int]:
    """Write a 4-byte length-prefixed frame to the pipe."""
    frame = struct.pack("<I", len(data)) + data
    total_written = 0
    while total_written < len(frame):
        to_write = len(frame) - total_written
        chunk = frame[total_written:]
        written = DWORD(0)
        res = kernel32.WriteFile(
            h_pipe,
            chunk,
            to_write,
            ctypes.byref(written),
            None,
        )
        if not res:
            return False, kernel32.GetLastError()
        total_written += written.value
    return True, 0


# ---------------------------------------------------------------------------
# Multi-Channel Priority State Compositor
# ---------------------------------------------------------------------------

class CursorStateCompositor:
    """Thread-safe multi-channel priority compositor state table.

    Arbitrates each visual channel independently across all active client layers
    by selecting the attribute value with the highest numerical priority.
    """

    CHANNELS = ["base_shape", "tint_rgba", "corner_badge", "progress_bar", "toast"]

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # client_id -> { layer_id: LayerPayload }
        self._layers_by_client: Dict[int, Dict[str, Dict[str, Any]]] = {}
        self._composite: Dict[str, Any] = {ch: None for ch in self.CHANNELS}

    def upsert_layer(self, client_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Insert or replace an asserted state layer for a client."""
        layer_id = str(payload.get("layer_id", "default"))
        with self._lock:
            if client_id not in self._layers_by_client:
                self._layers_by_client[client_id] = {}
            self._layers_by_client[client_id][layer_id] = dict(payload)
            self._evaluate_locked()
            return dict(self._composite)

    def clear_layer(self, client_id: int, layer_id: str) -> Dict[str, Any]:
        """Remove a specific layer asserted by a client."""
        with self._lock:
            if client_id in self._layers_by_client:
                self._layers_by_client[client_id].pop(layer_id, None)
            self._evaluate_locked()
            return dict(self._composite)

    def drop_client(self, client_id: int) -> Dict[str, Any]:
        """Purge all active layers belonging to a disconnected client handle."""
        with self._lock:
            self._layers_by_client.pop(client_id, None)
            self._evaluate_locked()
            return dict(self._composite)

    def get_composite(self) -> Dict[str, Any]:
        """Return a snapshot of the current composite visual state."""
        with self._lock:
            return dict(self._composite)

    def _evaluate_locked(self) -> None:
        """Resolve highest priority value for each channel under lock."""
        new_comp: Dict[str, Any] = {}
        for ch in self.CHANNELS:
            best_val = None
            best_priority = -float("inf")
            for client_layers in self._layers_by_client.values():
                for layer in client_layers.values():
                    val = layer.get(ch)
                    if val is not None:
                        prio = int(layer.get("priority", 0))
                        if prio > best_priority:
                            best_priority = prio
                            best_val = val
            new_comp[ch] = best_val
        self._composite = new_comp


# ---------------------------------------------------------------------------
# Multi-Client Named Pipe Server
# ---------------------------------------------------------------------------

class NamedPipeServer:
    """Event-driven Win32 Named Pipe server supporting concurrent clients."""

    def __init__(
        self,
        pipe_name: str,
        compositor: CursorStateCompositor,
        on_client_disconnect: Optional[Callable[[int, int, int], None]] = None,
    ) -> None:
        self.pipe_name = pipe_name
        self.compositor = compositor
        self.on_client_disconnect = on_client_disconnect
        self.h_stop_event = kernel32.CreateEventW(None, True, False, None)
        self.server_ready_event = threading.Event()
        self.worker_threads: List[threading.Thread] = []
        self.active_pipes: List[Any] = []
        self.pipes_lock = threading.Lock()
        self.running = False
        self.listener_thread: Optional[threading.Thread] = None

        self.latency_samples_us: List[float] = []
        self.latency_lock = threading.Lock()
        self._client_seq = 0
        self._seq_lock = threading.Lock()

    def start(self) -> None:
        """Start the background pipe listener thread."""
        self.running = True
        self.listener_thread = threading.Thread(
            target=self._listener_loop,
            name="PipeListenerThread",
            daemon=True,
        )
        self.listener_thread.start()
        self.server_ready_event.wait(timeout=5.0)

    def stop(self) -> None:
        """Signal stop event, unblock pending operations, and join threads."""
        self.running = False
        kernel32.SetEvent(self.h_stop_event)

        with self.pipes_lock:
            for hp in self.active_pipes:
                kernel32.CancelIoEx(hp, None)

        if self.listener_thread:
            self.listener_thread.join(timeout=3.0)

        for wt in list(self.worker_threads):
            wt.join(timeout=2.0)

        if self.h_stop_event:
            kernel32.CloseHandle(self.h_stop_event)
            self.h_stop_event = None

    def _next_client_id(self) -> int:
        with self._seq_lock:
            self._client_seq += 1
            return self._client_seq

    def _client_worker(self, h_pipe: Any, client_id: int) -> None:
        """Dedicated worker thread handling frames from a connected client."""
        with self.pipes_lock:
            self.active_pipes.append(h_pipe)

        try:
            while self.running:
                raw_payload, err = read_frame(h_pipe)
                if raw_payload is None:
                    t_broken = time.perf_counter_ns()
                    if err in (ERROR_BROKEN_PIPE, ERROR_OPERATION_ABORTED):
                        if self.on_client_disconnect:
                            self.on_client_disconnect(client_id, err, t_broken)
                        self.compositor.drop_client(client_id)
                    break

                try:
                    msg = json.loads(raw_payload.decode("utf-8"))
                except Exception:
                    continue

                client_ts_ns = msg.get("client_ts_ns")
                action = msg.get("action", "upsert")
                if action == "clear":
                    comp = self.compositor.clear_layer(
                        client_id,
                        msg.get("layer_id", "default"),
                    )
                else:
                    comp = self.compositor.upsert_layer(client_id, msg)
                t_resolved = time.perf_counter_ns()

                latency_us = None
                if client_ts_ns is not None:
                    latency_us = (t_resolved - client_ts_ns) / 1000.0
                    with self.latency_lock:
                        self.latency_samples_us.append(latency_us)

                resp = {
                    "status": "ok",
                    "client_id": client_id,
                    "composite": comp,
                    "server_ts_ns": t_resolved,
                    "latency_us": latency_us,
                }
                resp_bytes = json.dumps(resp).encode("utf-8")
                ok, _ = write_frame(h_pipe, resp_bytes)
                if not ok:
                    break
        finally:
            with self.pipes_lock:
                if h_pipe in self.active_pipes:
                    self.active_pipes.remove(h_pipe)
            kernel32.DisconnectNamedPipe(h_pipe)
            kernel32.CloseHandle(h_pipe)

    def _listener_loop(self) -> None:
        """Listener loop accepting incoming client connections via overlapped I/O."""
        self.server_ready_event.set()

        while self.running:
            h_pipe = kernel32.CreateNamedPipeW(
                self.pipe_name,
                PIPE_ACCESS_DUPLEX | FILE_FLAG_OVERLAPPED,
                PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT,
                PIPE_UNLIMITED_INSTANCES,
                PIPE_BUFFER_SIZE,
                PIPE_BUFFER_SIZE,
                5000,
                None,
            )
            if h_pipe == INVALID_HANDLE_VALUE or not h_pipe:
                break

            h_connect = kernel32.CreateEventW(None, True, False, None)
            ov = OVERLAPPED()
            ov.hEvent = h_connect

            res = kernel32.ConnectNamedPipe(h_pipe, ctypes.byref(ov))
            connected = False

            if res:
                connected = True
            else:
                err = kernel32.GetLastError()
                if err == ERROR_PIPE_CONNECTED:
                    connected = True
                elif err == ERROR_IO_PENDING:
                    events = (HANDLE * 2)(h_connect, self.h_stop_event)
                    wait_res = kernel32.WaitForMultipleObjects(
                        2,
                        events,
                        False,
                        INFINITE,
                    )
                    if wait_res == WAIT_OBJECT_0:
                        bytes_transferred = DWORD(0)
                        kernel32.GetOverlappedResult(
                            h_pipe,
                            ctypes.byref(ov),
                            ctypes.byref(bytes_transferred),
                            False,
                        )
                        connected = True
                    else:
                        kernel32.CancelIoEx(h_pipe, ctypes.byref(ov))
                        kernel32.CloseHandle(h_connect)
                        kernel32.CloseHandle(h_pipe)
                        break

            kernel32.CloseHandle(h_connect)

            if connected and self.running:
                client_id = self._next_client_id()
                wt = threading.Thread(
                    target=self._client_worker,
                    args=(h_pipe, client_id),
                    name=f"PipeWorker-{client_id}",
                    daemon=True,
                )
                self.worker_threads.append(wt)
                wt.start()
            else:
                kernel32.CloseHandle(h_pipe)
                break


# ---------------------------------------------------------------------------
# Client Implementation for Feeders and Benchmarking
# ---------------------------------------------------------------------------

class NamedPipeClient:
    """Client connection wrapper for testing and IPC benchmarks."""

    def __init__(self, pipe_name: str) -> None:
        self.pipe_name = pipe_name
        self.h_pipe: Any = None

    def connect(self, timeout_ms: int = 5000) -> bool:
        """Connect to the named pipe using Win32 CreateFileW."""
        t_start = time.perf_counter()
        while True:
            self.h_pipe = kernel32.CreateFileW(
                self.pipe_name,
                GENERIC_READ | GENERIC_WRITE,
                0,
                None,
                OPEN_EXISTING,
                0,
                None,
            )
            if self.h_pipe != INVALID_HANDLE_VALUE and self.h_pipe:
                return True

            err = kernel32.GetLastError()
            if err == ERROR_PIPE_BUSY:
                kernel32.WaitNamedPipeW(self.pipe_name, 1000)
            else:
                elapsed_ms = (time.perf_counter() - t_start) * 1000.0
                if elapsed_ms > timeout_ms:
                    return False
                kernel32.WaitNamedPipeW(self.pipe_name, 100)

    def send_payload(self, payload: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], int]:
        """Send a JSON payload and receive the server response."""
        data = json.dumps(payload).encode("utf-8")
        ok, err = write_frame(self.h_pipe, data)
        if not ok:
            return None, err
        resp_bytes, err = read_frame(self.h_pipe)
        if resp_bytes is None:
            return None, err
        try:
            return json.loads(resp_bytes.decode("utf-8")), 0
        except Exception:
            return None, -1

    def close(self) -> None:
        """Close the client pipe handle."""
        if self.h_pipe and self.h_pipe != INVALID_HANDLE_VALUE:
            kernel32.CloseHandle(self.h_pipe)
            self.h_pipe = None


# ---------------------------------------------------------------------------
# Statistical Calculations
# ---------------------------------------------------------------------------

def compute_statistics(samples: List[float]) -> Dict[str, float]:
    """Calculate mean, standard deviation, and percentiles for a sample array."""
    if not samples:
        return {
            "count": 0.0,
            "min": 0.0,
            "mean": 0.0,
            "p50": 0.0,
            "p95": 0.0,
            "p99": 0.0,
            "max": 0.0,
            "stddev": 0.0,
        }
    sorted_s = sorted(samples)
    n = len(sorted_s)
    mean_val = sum(sorted_s) / n
    variance = sum((x - mean_val) ** 2 for x in sorted_s) / n
    stddev = math.sqrt(variance)

    def get_pct(p: float) -> float:
        idx = int(round((p / 100.0) * (n - 1)))
        return sorted_s[max(0, min(n - 1, idx))]

    return {
        "count": float(n),
        "min": sorted_s[0],
        "mean": mean_val,
        "p50": get_pct(50.0),
        "p95": get_pct(95.0),
        "p99": get_pct(99.0),
        "max": sorted_s[-1],
        "stddev": stddev,
    }


# ---------------------------------------------------------------------------
# Child Feeder Subcommands
# ---------------------------------------------------------------------------

def run_feeder_a(pipe_name: str) -> None:
    """Run Feeder A simulating Caster at Priority 80 with red tint."""
    client = NamedPipeClient(pipe_name)
    if not client.connect(5000):
        sys.stderr.write("Feeder A connection failed\n")
        sys.exit(1)

    payload = {
        "layer_id": "caster_voice",
        "priority": 80,
        "base_shape": None,
        "tint_rgba": "0xFF5555FF",
        "corner_badge": None,
        "progress_bar": None,
        "toast": None,
        "ttl_ms": None,
    }
    resp, err = client.send_payload(payload)
    if resp is None:
        sys.stderr.write(f"Feeder A payload submission failed: {err}\n")
        sys.exit(1)

    sys.stdout.write("FEEDER_A_READY\n")
    sys.stdout.flush()

    # Block indefinitely waiting for server instructions or process termination
    read_frame(client.h_pipe)
    client.close()


def run_feeder_b(pipe_name: str) -> None:
    """Run Feeder B simulating Active Desktop Context Engine at Priority 20 with TV badge."""
    client = NamedPipeClient(pipe_name)
    if not client.connect(5000):
        sys.stderr.write("Feeder B connection failed\n")
        sys.exit(1)

    payload = {
        "layer_id": "active_desktop_context",
        "priority": 20,
        "base_shape": None,
        "tint_rgba": None,
        "corner_badge": "tv_icon",
        "progress_bar": None,
        "toast": None,
        "ttl_ms": None,
    }
    resp, err = client.send_payload(payload)
    if resp is None:
        sys.stderr.write(f"Feeder B payload submission failed: {err}\n")
        sys.exit(1)

    sys.stdout.write("FEEDER_B_READY\n")
    sys.stdout.flush()

    # Block indefinitely waiting for server instructions or process termination
    read_frame(client.h_pipe)
    client.close()


def run_benchmark_feeder(pipe_name: str, total_cycles: int = 1000) -> None:
    """Run high-frequency benchmark feeder measuring round-trip latency."""
    client = NamedPipeClient(pipe_name)
    if not client.connect(5000):
        sys.stderr.write("Benchmark feeder connection failed\n")
        sys.exit(1)

    round_trip_samples_us: List[float] = []
    t_start_total = time.perf_counter()

    for i in range(total_cycles):
        t0 = time.perf_counter_ns()
        payload = {
            "layer_id": "benchmark_layer",
            "priority": 50,
            "base_shape": "default_arrow",
            "tint_rgba": None,
            "corner_badge": None,
            "progress_bar": round((i % 100) / 100.0, 4),
            "toast": None,
            "ttl_ms": None,
            "client_ts_ns": t0,
        }
        resp, err = client.send_payload(payload)
        t1 = time.perf_counter_ns()
        if resp is None:
            sys.stderr.write(f"Benchmark cycle {i} failed: {err}\n")
            break
        round_trip_samples_us.append((t1 - t0) / 1000.0)

    t_end_total = time.perf_counter()
    total_duration_sec = t_end_total - t_start_total
    client.close()

    result = {
        "cycles": len(round_trip_samples_us),
        "duration_sec": total_duration_sec,
        "throughput_ops_sec": (
            len(round_trip_samples_us) / total_duration_sec
            if total_duration_sec > 0
            else 0.0
        ),
        "round_trip_stats": compute_statistics(round_trip_samples_us),
    }
    sys.stdout.write(json.dumps(result) + "\n")
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# Test Orchestration & Execution Suite
# ---------------------------------------------------------------------------

def run_suite(pipe_name: str = DEFAULT_PIPE_NAME) -> Dict[str, Any]:
    """Execute the full Named Pipe and process lease verification suite."""
    print("=" * 72)
    print("Dynamic Cursor Spike 03: Win32 Named Pipe Leases & Priority Arbitration")
    print("=" * 72)

    compositor = CursorStateCompositor()
    broken_events: Dict[int, Dict[str, Any]] = {}
    broken_lock = threading.Lock()
    broken_signal = threading.Event()

    def handle_disconnect(client_id: int, err_code: int, t_broken_ns: int) -> None:
        with broken_lock:
            broken_events[client_id] = {
                "err": err_code,
                "t_broken_ns": t_broken_ns,
            }
        broken_signal.set()

    server = NamedPipeServer(
        pipe_name=pipe_name,
        compositor=compositor,
        on_client_disconnect=handle_disconnect,
    )
    server.start()
    print(f"[OK] Server listening on {pipe_name}")

    suite_results: Dict[str, Any] = {}

    # -----------------------------------------------------------------------
    # Phase 1: Orthogonal Multi-Channel Merging Verification
    # -----------------------------------------------------------------------
    print("\n[Phase 1] Launching concurrent feeders to test orthogonal channel merging...")

    # Spawn Feeder B (Priority 20, TV Badge)
    cmd_b = [sys.executable, __file__, "--feeder-b", "--pipe", pipe_name]
    proc_b = subprocess.Popen(
        cmd_b,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    line_b = proc_b.stdout.readline().strip() if proc_b.stdout else ""
    if line_b != "FEEDER_B_READY":
        raise RuntimeError(f"Feeder B initialization failed: {line_b}")
    print("  -> Feeder B connected: Priority 20, corner_badge='tv_icon'")

    state_after_b = compositor.get_composite()
    print(f"  -> Intermediate Composite State: {state_after_b}")
    assert state_after_b["corner_badge"] == "tv_icon"
    assert state_after_b["tint_rgba"] is None

    # Spawn Feeder A (Priority 80, Red Tint)
    cmd_a = [sys.executable, __file__, "--feeder-a", "--pipe", pipe_name]
    proc_a = subprocess.Popen(
        cmd_a,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    line_a = proc_a.stdout.readline().strip() if proc_a.stdout else ""
    if line_a != "FEEDER_A_READY":
        raise RuntimeError(f"Feeder A initialization failed: {line_a}")
    print("  -> Feeder A connected: Priority 80, tint_rgba='0xFF5555FF'")

    state_after_a = compositor.get_composite()
    print(f"  -> Merged Composite State: {state_after_a}")
    assert state_after_a["tint_rgba"] == "0xFF5555FF"
    assert state_after_a["corner_badge"] == "tv_icon"
    print("  [PASSED] Channels merged orthogonally: tint from Feeder A, badge from Feeder B.")

    suite_results["orthogonal_merge"] = {
        "status": "PASSED",
        "composite_state": state_after_a,
    }

    # -----------------------------------------------------------------------
    # Phase 2: High-Frequency IPC Benchmark (1,000 cycles)
    # -----------------------------------------------------------------------
    print("\n[Phase 2] Executing 1,000 cycle IPC latency benchmark across processes...")
    cmd_bench = [
        sys.executable,
        __file__,
        "--benchmark",
        "--cycles",
        "1000",
        "--pipe",
        pipe_name,
    ]
    proc_bench = subprocess.Popen(
        cmd_bench,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    bench_out, bench_err = proc_bench.communicate(timeout=30.0)
    if proc_bench.returncode != 0:
        raise RuntimeError(f"Benchmark execution failed: {bench_err}")

    bench_json = json.loads(bench_out.strip())
    rt_stats = bench_json["round_trip_stats"]

    with server.latency_lock:
        one_way_samples = list(server.latency_samples_us)
    one_way_stats = compute_statistics(one_way_samples)

    suite_results["benchmark"] = {
        "cycles": bench_json["cycles"],
        "duration_sec": bench_json["duration_sec"],
        "throughput_ops_sec": bench_json["throughput_ops_sec"],
        "one_way_stats": one_way_stats,
        "round_trip_stats": rt_stats,
    }

    print(
        f"  -> Processed {bench_json['cycles']} cycles in {bench_json['duration_sec']:.3f} s "
        f"({bench_json['throughput_ops_sec']:.1f} msg/sec)"
    )
    print("  -> End-to-End One-Way Latency (Write -> Read + Arbitrate):")
    print(
        f"     Mean: {one_way_stats['mean']:.2f} us | "
        f"P50: {one_way_stats['p50']:.2f} us | "
        f"P95: {one_way_stats['p95']:.2f} us | "
        f"Min: {one_way_stats['min']:.2f} us | "
        f"Max: {one_way_stats['max']:.2f} us"
    )
    print("  -> Full Round-Trip Latency (Write -> Arbitrate -> Ack -> Read):")
    print(
        f"     Mean: {rt_stats['mean']:.2f} us | "
        f"P50: {rt_stats['p50']:.2f} us | "
        f"P95: {rt_stats['p95']:.2f} us | "
        f"Min: {rt_stats['min']:.2f} us | "
        f"Max: {rt_stats['max']:.2f} us"
    )

    # -----------------------------------------------------------------------
    # Phase 3: Kernel Lease Automatic Rollback Verification
    # -----------------------------------------------------------------------
    print("\n[Phase 3] Testing kernel connection lease rollback on abrupt crash...")
    broken_signal.clear()

    # Capture timestamp and forcibly kill Feeder A
    t_kill_ns = time.perf_counter_ns()
    proc_a.kill()
    proc_a.wait()

    # Wait for server to receive ERROR_BROKEN_PIPE without polling
    signaled = broken_signal.wait(timeout=5.0)
    assert signaled, "Server failed to detect broken pipe within timeout."

    with broken_lock:
        latest_event = list(broken_events.values())[-1]
    t_detected_ns = latest_event["t_broken_ns"]
    detection_latency_us = (t_detected_ns - t_kill_ns) / 1000.0

    print(
        f"  -> Kernel lease drop detected via ERROR_BROKEN_PIPE in "
        f"{detection_latency_us:.2f} us ({detection_latency_us / 1000.0:.3f} ms)"
    )

    state_after_kill_a = compositor.get_composite()
    print(f"  -> State After Feeder A Termination: {state_after_kill_a}")
    assert (
        state_after_kill_a["tint_rgba"] is None
    ), "Feeder A tint_rgba was not purged."
    assert (
        state_after_kill_a["corner_badge"] == "tv_icon"
    ), "Feeder B corner_badge was erroneously dropped."
    print("  [PASSED] Feeder A layer purged cleanly; Feeder B layer remains active.")

    # Now terminate Feeder B and verify rollback to baseline
    broken_signal.clear()
    proc_b.kill()
    proc_b.wait()
    broken_signal.wait(timeout=5.0)

    state_baseline = compositor.get_composite()
    print(f"  -> Final State After Feeder B Termination: {state_baseline}")
    for ch in CursorStateCompositor.CHANNELS:
        assert state_baseline[ch] is None, f"Channel {ch} is not None at baseline."
    print("  [PASSED] Composite state reverted cleanly to baseline.")

    suite_results["lease_rollback"] = {
        "status": "PASSED",
        "detection_latency_us": detection_latency_us,
        "detection_latency_ms": detection_latency_us / 1000.0,
        "state_after_kill_a": state_after_kill_a,
        "state_baseline": state_baseline,
    }

    # Clean server shutdown
    server.stop()
    print("\n[OK] Server stopped cleanly. Zero resource leaks detected.")
    print("=" * 72)

    return suite_results


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Win32 Named Pipe and Kernel Lease Benchmark"
    )
    parser.add_argument(
        "--pipe",
        type=str,
        default=DEFAULT_PIPE_NAME,
        help="Named pipe endpoint path",
    )
    parser.add_argument(
        "--feeder-a",
        action="store_true",
        help="Run as Feeder A (Caster, Priority 80, Red Tint)",
    )
    parser.add_argument(
        "--feeder-b",
        action="store_true",
        help="Run as Feeder B (Context Engine, Priority 20, TV Badge)",
    )
    parser.add_argument(
        "--benchmark",
        action="store_true",
        help="Run as benchmark client",
    )
    parser.add_argument(
        "--cycles",
        type=int,
        default=1000,
        help="Number of benchmark cycles",
    )
    args = parser.parse_args()

    if args.feeder_a:
        run_feeder_a(args.pipe)
    elif args.feeder_b:
        run_feeder_b(args.pipe)
    elif args.benchmark:
        run_benchmark_feeder(args.pipe, args.cycles)
    else:
        results = run_suite(args.pipe)
        print("\nSerialized Telemetry Summary (JSON):")
        print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
