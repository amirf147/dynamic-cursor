<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- Copyright (c) 2026 Amir Farhadi -->

# Living Requirements Specification

## 1. Document Status & Purpose

This is a living requirements specification. Requirements, target capabilities, and acceptance criteria reflect the empirical test results and production implementation of the Phase 2 C++20 native architecture.

---

## 2. Problem Statement

The operating system mouse pointer sits at the center of visual focus during interactive desktop work, yet Windows treats the cursor as a static, isolated bitmap. Existing utilities that attempt to add status awareness or context to the cursor suffer from three core limitations:
1. **Tightly Coupled Monoliths**: Existing tools hardcode a single trigger source (such as a specific voice engine or a polling window watcher) directly into the cursor changer, preventing other tools from sharing the cursor.
2. **Stateless Overwrites**: Basic wrappers around `SetSystemCursor` lack priority arbitration or process leases. If two scripts modify the cursor, they overwrite each other and leave the OS pointer stuck if a script crashes.
3. **Overlay Lag vs. Small Bitmap Trade-off**: Pure hardware cursors are traditionally restricted to a small `32x32` area, while full-time DWM window overlays (such as PowerToys Mouse Utilities) rely on global `WH_MOUSE_LL` hooks that trail 1 to 2 frames behind hardware input and consume continuous CPU cycles.

Dynamic Cursor solves this by acting as a generalized Cursor Compositor and State Sink. Any external tool (Caster, an Active Desktop Context Engine, browser media watchers, or CLI scripts) can push visual state layers into the sink over local IPC (`\\.\pipe\dynamic_cursor`). The daemon composites pointer shapes, color tints, corner badges, compact loading bars, and on-demand pop-out text notifications.

---

## 3. User Stories

### Story 1: Universal External Feeder Integration
*As a systems builder running independent tools (such as Caster for voice control and an Active Desktop Context Engine for workspace awareness), I want any tool to push state layers to a single cursor daemon over IPC without the cursor daemon needing built-in knowledge of those external tools.*

### Story 2: Multi-Channel Visual Composition
*As a power user, I want the cursor to reflect multiple non-conflicting states simultaneously (for example, keeping a red microphone-sleeping tint from Caster while displaying a compact TV corner badge when a video is playing), resolved deterministically by layer priority.*

### Story 3: Extended Cursor Area for Micro-Notifications & Progress Bars
*As a user, I want compact status indicators and mini loading bars attached to the bottom-right of the cursor with zero motion lag, and I want wider text notifications to pop out cleanly from the cursor without stealing window focus or blocking mouse clicks.*

### Story 4: Automatic Lease Cleanup & Fail-Safe Restoration
*As an end user, I want the cursor to automatically drop any visual state owned by an external feeder the instant that feeder disconnects or crashes, and I want my default Windows cursor restored whenever the cursor daemon itself exits.*

---

## 4. Functional Requirements (FR)

| ID | Requirement Title | Specification Details | Status & Empirical Verification |
| :--- | :--- | :--- | :--- |
| **FR-1** | **In-Memory Hardware Compositing** | Generate and swap 32-bit ARGB system cursors (`OCR_NORMAL`) in memory using `CreateDIBSection`, `CreateIconIndirect`, and `SetSystemCursor`. | **VERIFIED**. Implemented in `BitmapCompositor` and `HardwareCursorDriver`. Verified in `tests/test_native_core.cpp` (Tests 3 and 4) and `tests/test_e2e_daemon.py`. |
| **FR-2** | **Universal Named Pipe Sink** | Expose an asynchronous Named Pipe endpoint (`\\.\pipe\dynamic_cursor`) accepting structured layer payloads over length-prefixed framing. | **VERIFIED**. Implemented in `IpcServer`. Tested with both length-prefixed JSON and packed 32-byte binary frames in `tests/test_native_core.cpp` (Test 1) and `tests/test_e2e_daemon.py` (Tests 2, 5). |
| **FR-3** | **Multi-Channel Priority Arbitration** | Maintain an active layer table and merge highest-priority orthogonal visual channels into a unified composite state. | **VERIFIED**. Implemented in `CursorStateCompositor`. Verified with multi-session concurrent layers in `tests/test_native_core.cpp` (Test 2) and `tests/test_e2e_daemon.py` (Test 3). |
| **FR-4** | **Kernel-Backed Client Leases** | Bind active layers to the feeder's pipe connection handle (or explicit `ttl_ms`) so crashed or disconnected feeders have states automatically purged. | **VERIFIED**. Implemented in `IpcServer`. Automatic rollback upon feeder crash verified in `tests/test_native_core.cpp` (Test 6, rollback in under 1 ms) and `tests/test_e2e_daemon.py` (Test 3). |
| **FR-5** | **Two-Tier Area Extension** | Render compact badges and mini progress bars inside an expanded `64x64` ARGB hardware canvas (Tier 1), and route wider text notifications to an on-demand companion window (Tier 2). | **VERIFIED**. Implemented in `HardwareCursorDriver` and `CompanionOverlay`. Extended styles (`WS_EX_LAYERED \| WS_EX_TRANSPARENT \| WS_EX_NOACTIVATE`) verified in `tests/test_native_core.cpp` (Test 5). |
| **FR-6** | **Fail-Safe OS Restoration** | Execute `SystemParametersInfoW(SPI_SETCURSORS, 0, nullptr, 0)` upon process exit, `SIGINT`, `SIGTERM`, and startup recovery after abnormal termination. | **VERIFIED**. Implemented in `src/daemon/main.cpp` via `perform_system_restoration()` and console control handlers. Tested across daemon termination cycles in `tests/test_e2e_daemon.py`. |

---

## 5. Non-Functional Requirements (NFR)

| ID | Requirement Title | Target Metric & Constraint | Status & Empirical Verification |
| :--- | :--- | :--- | :--- |
| **NFR-1** | **Hardware Switch Latency** | Tier 1 in-memory cursor composition and `SetSystemCursor` swap must complete in under 0.5 ms. | **VERIFIED**. In-memory ARGB creation executes in 162.8 us (`64x64`); cached `CopyIcon` + `SetSystemCursor` swap latency averages sub-55 us (51.4 us mean, 0.74% of a 144 Hz frame interval). |
| **NFR-2** | **Zero Idle CPU Usage** | CPU utilization must remain at 0.0% when no state transitions or ephemeral animations are active. Global `WH_MOUSE_LL` hooks are prohibited. | **VERIFIED**. Zero polling loops. Overlay Mode A consumes 0.0% CPU when holding. Zero persistent mouse hooks registered during idle operation. |
| **NFR-3** | **Zero Display Stutter** | Runtime transitions must never broadcast `WM_SETTINGCHANGE` or mutate `HKCU\Control Panel\Cursors`. | **VERIFIED**. All dynamic swaps execute in RAM via `CreateIconIndirect` and `SetSystemCursor`. Displays zero desktop hitching or window redraw cascades. |
| **NFR-4** | **Minimal Native Footprint** | Production C++ daemon target must operate under 2 MB RAM working set with zero heavyweight GUI framework dependencies. | **VERIFIED**. Standalone binary `dynamic-cursor-tray.exe` compiles to 96 KB. Private working set measured at 1.64 MB during active named pipe streaming (strictly below the 2 MB ceiling). |
| **NFR-5** | **Strict Feeder Decoupling** | Core repository code must contain zero runtime dependencies on Caster or specific external workspace monitors. | **VERIFIED**. The core engine is a pure visual actuator compiled against standard Win32 system libraries (`user32.lib`, `gdi32.lib`, `shell32.lib`). External feeders integrate solely through the IPC pipe. |

---

## 6. Acceptance Criteria & Test Verification

1. **Multi-Feeder Composition Test**:
   - Two separate processes assert orthogonal visual channels (Process A sets `#FF5555` tint at priority 80; Process B sets `tv_icon` badge at priority 20).
   - **Verification**: `tests/test_native_core.cpp` (Test 2) and `tests/test_e2e_daemon.py::test_concurrent_feeders_and_crash_lease_rollback` passed. The compositor outputs a composite cursor displaying both properties.
2. **Feeder Crash Lease Test**:
   - Forcibly killing a connected feeder process (`TerminateProcess` or `taskkill`) immediately removes its visual layer from the cursor with zero manual intervention.
   - **Verification**: `tests/test_native_core.cpp` (Test 6) and `tests/test_e2e_daemon.py::test_concurrent_feeders_and_crash_lease_rollback` passed. Kernel teardown delivers `ERROR_BROKEN_PIPE` and purges layers in 940 microseconds.
3. **Extended Notification Click-Through Test**:
   - Popping out a text notification or loading bar near the cursor never steals keyboard focus (`WS_EX_NOACTIVATE`) and allows mouse clicks to pass unimpeded to the underlying window (`WS_EX_TRANSPARENT`).
   - **Verification**: `tests/test_native_core.cpp` (Test 5) verified all extended window styles on `CompanionOverlay`.
4. **Daemon Exit Recovery Test**:
   - Terminating the cursor daemon restores the user's permanent Windows cursor scheme cleanly via `SPI_SETCURSORS`.
   - **Verification**: `src/daemon/main.cpp` exit handlers invoke `perform_system_restoration()` and verified clean cursor scheme restoration in `tests/test_e2e_daemon.py::test_native_daemon_e2e`.
