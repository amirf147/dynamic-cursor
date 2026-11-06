<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- Copyright (c) 2026 Amir Farhadi -->

# Spike 03 Report: Win32 Named Pipe IPC, Priority Compositor, and Kernel Leases

## 1. Executive Summary

This investigation evaluates local inter-process communication (IPC) throughput, priority state arbitration, and kernel connection lease recovery for the `dynamic-cursor` architecture. External producers, such as Caster voice recognition and the Active Desktop Context Engine, must update cursor visual channels asynchronously without introducing visual stutter or polling overhead.

The empirical benchmark script [spikes/spike_03_named_pipe_leases.py](../../spikes/spike_03_named_pipe_leases.py) executes a multi-client Named Pipe server at `\\.\pipe\dynamic_cursor_spike`. The benchmark tests multi-channel priority arbitration across concurrent out-of-process feeders, records microsecond-level IPC latencies across 1,000 message cycles, and measures kernel lease teardown duration upon abrupt process termination.

The empirical results confirm that Win32 Named Pipes provide sub-50-microsecond round-trip latency, high throughput exceeding 21,700 operations per second in Python ctypes, and sub-millisecond kernel cleanup (0.940 ms) via OS handle table teardown.

---

## 2. Wire Protocol Specification & Framing

### 2.1 Framing Architecture

Three framing models were evaluated for the local IPC channel:
1. **Newline-Delimited JSON (NDJSON):** Requires scanning incoming byte buffers sequentially for byte value `0x0A` (`\n`). Buffer fragmentation across reads necessitates dynamic reallocation and string parsing overhead.
2. **Win32 Message-Mode Pipes (`PIPE_TYPE_MESSAGE`):** Preserves message boundaries at the OS kernel driver level. However, reading message buffers that exceed the caller buffer size yields `ERROR_MORE_DATA` (234), adding multi-step buffer resizing logic.
3. **Length-Prefixed Framing (Selected):** Each frame begins with a fixed 4-byte unsigned 32-bit integer in little-endian byte order (`uint32_t`), indicating the exact byte length of the subsequent payload.

```
+---------------------------+-----------------------------------------------+
|  Payload Length (4 bytes) |          UTF-8 Encoded JSON Payload           |
|     uint32_t (Little)     |              Variable (N bytes)               |
+---------------------------+-----------------------------------------------+
```

Length-prefixed framing provides binary safety, deterministic allocation, and compatibility with standard C++20 network buffers without requiring stream delimiter scanning.

### 2.2 Wire Schema

The client request payload conforms to the following JSON schema:

```json
{
  "layer_id": "caster_voice",
  "priority": 80,
  "base_shape": null,
  "tint_rgba": "0xFF5555FF",
  "corner_badge": null,
  "progress_bar": null,
  "toast": null,
  "ttl_ms": null,
  "action": "upsert",
  "client_ts_ns": 1730910000000000
}
```

| Field | Type | Description |
| :--- | :--- | :--- |
| `layer_id` | `string` | Unique identifier for the layer asserted by the client connection. |
| `priority` | `integer` | Numerical arbitration priority (higher values supersede lower values). |
| `base_shape` | `string \| null` | Requested cursor shape (`default_arrow`, `precision_cross`, etc.). |
| `tint_rgba` | `string \| integer \| null` | Pointer tint in hexadecimal or 32-bit packed integer format. |
| `corner_badge` | `string \| null` | Badge sprite identifier stamped in the lower-right quadrant. |
| `progress_bar` | `float \| null` | Compact loading indicator ratio normalized between `0.0` and `1.0`. |
| `toast` | `object \| null` | Ephemeral micro-notification descriptor routed to Tier 2 overlay. |
| `ttl_ms` | `integer \| null` | Optional duration in milliseconds for one-shot stateless layers. |
| `action` | `string` | Operation type: `"upsert"` (default) or `"clear"`. |
| `client_ts_ns` | `integer` | High-precision nanosecond timestamp for latency benchmarking. |

The server acknowledgment payload conforms to:

```json
{
  "status": "ok",
  "client_id": 1,
  "composite": {
    "base_shape": null,
    "tint_rgba": "0xFF5555FF",
    "corner_badge": "tv_icon",
    "progress_bar": null,
    "toast": null
  },
  "server_ts_ns": 1730910000025450,
  "latency_us": 25.45
}
```

---

## 3. Multi-Channel Priority State Compositor Verification

The compositor tracks active layers per client connection handle. Visual channels are completely orthogonal; each channel resolves to the value supplied by the highest priority active layer across all connected clients.

```
Feeder A (Caster, Priority 80):
  tint_rgba:    0xFF5555FF (Red)
  corner_badge: null

Feeder B (Context Engine, Priority 20):
  tint_rgba:    null
  corner_badge: "tv_icon"

Merged Composite State:
  tint_rgba:    0xFF5555FF (from Feeder A)
  corner_badge: "tv_icon"   (from Feeder B)
```

The empirical test suite verified this behavior using two concurrent child processes:
1. Feeder B connected first and asserted `corner_badge: "tv_icon"` at Priority 20. The composite state reflected only the TV badge.
2. Feeder A connected concurrently and asserted `tint_rgba: "0xFF5555FF"` at Priority 80.
3. The composite state merged both channels, retaining the TV badge from Feeder B while applying the red tint from Feeder A.

---

## 4. IPC Latency & Throughput Benchmark

The benchmark executed 1,000 synchronous round-trip cycles between an independent feeder process and the Named Pipe server. All operations crossed genuine OS process boundaries and address spaces.

### 4.1 Latency Distribution Table (1,000 Cycles)

| Measurement Stage | Samples | Min (µs) | Mean (µs) | Median P50 (µs) | P95 (µs) | P99 (µs) | Max (µs) | Std Dev (µs) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **One-Way (Write -> Read + Arbitrate)** | 1,000 | 17.80 | 25.45 | 23.00 | 37.50 | 56.40 | 311.30 | 11.87 |
| **Full Round-Trip (Write -> Ack -> Read)** | 1,000 | 34.70 | 45.62 | 41.60 | 66.60 | 94.30 | 375.30 | 15.59 |

### 4.2 Throughput Performance

- **Execution Duration:** 0.0459 seconds for 1,000 sequential cycles.
- **Message Throughput:** 21,767.8 messages per second in interpreted Python ctypes.
- **CPU Resource Profile:** 0.0% CPU usage while idle between frames; execution remains purely kernel wait-state bound.

Because the production C++20 server avoids Python interpreter overhead and dynamic allocations, native end-to-end latency is projected to remain below 10 microseconds with throughput exceeding 100,000 operations per second.

---

## 5. Kernel Lease Automatic Rollback Mechanics

### 5.1 Failure Scenario & OS Handle Cleanup

A core architectural vulnerability in desktop state utilities is orphaned visual state. If a feeder process crashes while asserting a state layer, the cursor must not remain permanently frozen in that state.

The connection lease model binds layer lifetimes directly to the kernel handle of the client's Named Pipe connection:
1. When Feeder A is abruptly terminated via `TerminateProcess`, the Windows Object Manager closes all open kernel handles in the process handle table.
2. The server's pipe instance immediately transitions into broken pipe state (`FILE_PIPE_DISCONNECTED_STATE`).
3. The server worker thread, blocked in `ReadFile`, unblocks immediately with return value `FALSE` and `GetLastError() == ERROR_BROKEN_PIPE` (109).
4. The server compositor purges all layers associated with that client identifier and re-evaluates the composite state.

```mermaid
sequenceDiagram
    autonumber
    participant FA as Feeder A (Priority 80)
    participant FB as Feeder B (Priority 20)
    participant Kernel as Windows Kernel / Object Mgr
    participant Srv as Named Pipe Server Worker
    participant Comp as CursorStateCompositor

    Note over FA,FB: Both feeders active; composite has Red Tint + TV Badge
    Kernel->>FA: TerminateProcess(proc_a)
    Kernel->>Srv: Close handle table -> Pipe Broken
    Srv->>Srv: ReadFile fails with ERROR_BROKEN_PIPE (109)
    Srv->>Comp: drop_client(client_id_A)
    Comp->>Comp: Re-evaluate channels
    Note over Comp: Red tint removed; TV badge retained
    Srv->>Kernel: DisconnectNamedPipe & CloseHandle
```

### 5.2 Rollback Detection Latency Telemetry

- **Crash Trigger Mechanism:** Process killed via Win32 `TerminateProcess` (`proc_a.kill()`).
- **Detection Latency:** 940.10 microseconds (0.940 ms).
- **Composite State After Feeder A Termination:**
  `{'base_shape': None, 'tint_rgba': None, 'corner_badge': 'tv_icon', 'progress_bar': None, 'toast': None}`
- **Composite State After Feeder B Termination:**
  `{'base_shape': None, 'tint_rgba': None, 'corner_badge': None, 'progress_bar': None, 'toast': None}`

The test confirmed that Feeder A's red tint was dropped within 0.940 milliseconds, while Feeder B's TV badge remained uninterrupted. Terminating Feeder B restored the cursor to its neutral baseline state.

---

## 6. Concurrency Safety & Synchronization

### 6.1 Zero Polling Invariant

The server implementation contains zero sleep loops or periodic polling timers.
- **Connection Listening:** The listener thread invokes `ConnectNamedPipe` with an `OVERLAPPED` structure. It blocks in the Windows kernel using `WaitForMultipleObjects` on the connection event handle and a manual-reset shutdown event handle.
- **Data Reception:** Client worker threads block inside `ReadFile`. The thread remains suspended by the OS scheduler until data arrives or the client disconnects.
- **Clean Teardown:** Setting the shutdown event handle wakes the listener thread immediately. Active worker pipes are unblocked cleanly via `CancelIoEx(hPipe, NULL)`.

### 6.2 Compositor Synchronization

The compositor guards its layer table and composite cache with an internal mutex (`threading.Lock` in Python, mapping to `std::mutex` or `SRWLOCK` in C++20). The critical section is restricted to in-memory dictionary updates and priority resolution, completing in less than 2 microseconds per update.

---

## 7. Recommendations for Production C++20 Server

1. **I/O Completion Ports (IOCP) Architecture:**
   While the prototype uses a thread-per-client model suitable for 2 to 10 concurrent feeders, the production C++20 daemon should use Win32 I/O Completion Ports (`CreateIoCompletionPort`, `GetQueuedCompletionStatus`). A single completion port serviced by a thread pool of 2 worker threads can manage hundreds of concurrent pipe connections with zero per-connection thread overhead.
2. **Security Descriptor Hardening:**
   To prevent unprivileged processes or secondary user sessions on multi-user systems from manipulating the cursor, the pipe must be created with an explicit Security Descriptor (`SECURITY_ATTRIBUTES`). The DACL should restrict read and write access strictly to the current user's security identifier (`SDDL_CURRENT_USER_ONLY` / `"D:(A;;GA;;;PS)"`).
3. **Coalescing Rapid Updates:**
   Feeders transmitting rapid updates (such as high-frequency audio volume meters or mouse scrubbers) should not overwhelm the GPU cursor plane with hundreds of bitmap swaps per second. The compositor should enforce a minimum presentation interval (e.g. 8 ms, matching 120 Hz) for hardware bitmap regeneration while keeping the IPC receive buffer drained.
4. **Zero-Copy FlatBuffer or Compact Binary Wire Format:**
   For the C++20 implementation, JSON parsing can be replaced or augmented with a compact 32-byte binary struct for standard shape, tint, and badge updates, reserving JSON for extended toast payloads. This eliminates serialization overhead entirely.
