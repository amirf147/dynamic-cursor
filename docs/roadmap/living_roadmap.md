<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- Copyright (c) 2026 Amir Farhadi -->

# Living Implementation Roadmap

## 1. Roadmap Philosophy: Discovery-Driven Horizon

This roadmap balances near-term empirical validation spikes with incremental production deliverables. Low-level Win32 and WDDM behaviors empirically validated in Phase 1 established the technical boundaries for the Phase 2 production C++20 core engine.

```mermaid
flowchart LR
    P1["Phase 1: Empirical Discovery Spikes\n(COMPLETED)"] --> P2["Phase 2: Production C++20 Core & Dual Targets\n(COMPLETED)"]
    P2 --> P3["Phase 3: Procedural Animation & DPI Scaling\n(CURRENT HORIZON)"]
```

---

## 2. Phase 1: Completed Empirical Discovery Spikes

All Phase 1 discovery spikes have been empirically executed and validated on native Windows 11:

- [x] **Spike 1.1: Procedural In-Memory Cursor Compositing (`CreateDIBSection` + `CreateIconIndirect`)**
  - **Empirical Metric**: In-memory ARGB creation executed in 105.3 us (`32x32`) and 162.8 us (`64x64`); cached `CopyIcon` + `SetSystemCursor` swap latency measured at 40.7 us (`32x32`) and 51.4 us (`64x64`).
  - **Key Finding**: Pre-multiplied ARGB is strictly required by the WDDM scanout plane to avoid black border fringing. A 15-frame progress bar animation completed at 51.9 us per frame swap with zero hitching. The handle duplication protocol was confirmed; passing master cached handles directly causes Win32 Error 1402 because `SetSystemCursor` takes ownership and destroys the input handle.
  - **Report**: [docs/spikes/report_01_hardware_canvas.md](../spikes/report_01_hardware_canvas.md).

- [x] **Spike 1.2: Hardware Cursor Canvas Size Ceiling (`64x64`, `128x128`, `256x256`)**
  - **Empirical Metric**: Windows 11 and WDDM 3.x allocate exact requested dimensions at 32 bpp ARGB without downscaling or cropping. Reading back pixels via `GetDIBits` from `LoadCursorW(nullptr, OCR_NORMAL)` verified pixel fidelity at both hotspot `(0, 0)` and the extreme corner `(W - 1, H - 1)`.
  - **Architectural Decision**: Tier 1 standardizes on a `64x64` canvas (with `128x128` available for wide badges). A `64x64` canvas provides ample room for the primary arrow pointer, a `56x6` progress bar beneath the arrow, and a corner badge while consuming only 0.74% of the frame interval on a 144 Hz display panel (6.94 ms frame period).
  - **Report**: [docs/spikes/report_01_hardware_canvas.md](../spikes/report_01_hardware_canvas.md).

- [x] **Spike 1.3: Extended Cursor Area Mechanisms (Tier 2 Companion Window)**
  - **Empirical Metric**: Mode A (Spawn-Anchored Toast) achieved 353.7 us median spawn latency, 0.008% to 0.048% CPU during display, 0.0% CPU during hold, and zero active mouse hooks. Mode B (On-Demand Raw Input Follower) achieved 55.7 us registration latency, 30.4 us unregister latency, 74.0 us median `SetWindowPos` repositioning latency, and zero lingering hooks on teardown.
  - **Invariant Verification**: Programmatic testing confirmed that `GetForegroundWindow()` remained strictly unchanged across overlay lifecycles (`WS_EX_NOACTIVATE` integrity), and `WM_NCHITTEST` returned `HTTRANSPARENT` (`-1`) while `WindowFromPoint` returned the underlying window (`WS_EX_TRANSPARENT` integrity).
  - **Architectural Decision**: Adopt Mode A as the default presentation for text-heavy notifications and feeder alerts to eliminate text jitter during mouse motion. Reserve Mode B strictly for explicit requests for attached continuous HUDs.
  - **Report**: [docs/spikes/report_02_companion_overlay.md](../spikes/report_02_companion_overlay.md).

- [x] **Spike 1.4: Named Pipe Sink & Kernel Lease Auto-Rollback**
  - **Empirical Metric**: Length-prefixed Win32 Named Pipe server achieved 21,767.8 messages per second in interpreted Python ctypes, with 25.45 us one-way arbitration latency (mean) and 45.62 us full round-trip latency.
  - **Key Finding**: Merged orthogonal channels from Feeder A (Priority 80, red tint) and Feeder B (Priority 20, TV badge) cleanly into a single composite state. Forcible termination of Feeder A via `TerminateProcess` triggered kernel handle teardown, delivering `ERROR_BROKEN_PIPE` to the server in 940.10 us (0.94 ms) and automatically rolling back Feeder A's layer while preserving Feeder B's badge.
  - **Report**: [docs/spikes/report_03_named_pipe_leases.md](../spikes/report_03_named_pipe_leases.md).

- [x] **Spike 1.5: Native C++20 Toolchain & Windhawk Architecture**
  - **Empirical Metric**: Host reconnaissance identified MSVC 19.50 (`cl.exe`), Windows 11 SDK `10.0.26100.0`, CMake 3.31, Ninja 1.12, and Windhawk v1.5 with Clang 20.1. Minimal C++20 Win32 cursor generation benchmark compiled to 262 KB with MSVC, consuming 1.33 MB private working set with 42.56 us handle cycle latency (~23,000 cycles/sec).
  - **Architectural Decision**: Adopt a shared C++20 core engine supporting dual distribution targets: a standalone system tray daemon (`dynamic-cursor-tray.exe`) and a Windhawk mod (`dynamic-cursor.wh.cpp`) intercepting `SetCursor` in `user32.dll` via `Wh_SetFunctionHook` for apps that override `OCR_NORMAL`.
  - **Report**: [docs/spikes/report_04_native_toolchain.md](../spikes/report_04_native_toolchain.md).

---

## 3. Phase 2: Production C++20 Implementation Tasks (COMPLETED)

All Phase 2 implementation tasks have been completed and verified across native unit test and integration suites:

- [x] **Task 2.1: Wire Protocol & Framing Specification**
  - **Empirical Result**: Length-prefixed framing (4-byte little-endian length prefix) and packed 32-byte binary struct (`BinaryPacket`) verified in `tests/test_native_core.cpp` (Test 1). Embedded JSON parser (`json_internal::MiniParser`) deserializes color tokens, badges, shapes, progress ratios, and toasts with zero external dependencies.
  - **Specification**: [`docs/architecture/03_ipc_wire_protocol_and_sdk.md`](../architecture/03_ipc_wire_protocol_and_sdk.md).

- [x] **Task 2.2: Shared Native C++20 Core Engine**
  - **Empirical Result**: Compiled with CMake 3.31, Ninja, and MSVC 19.50 using `/std:c++20 /O2 /GL /LTCG`. Asynchronous overlapped Named Pipe server (`IpcServer`), Multi-Channel Priority Compositor (`CursorStateCompositor`), in-memory ARGB DIBSection compositor (`BitmapCompositor`), and RAII handle wrappers (`raii_handles.hpp`) verified in `tests/test_native_core.cpp` (Tests 1, 2, 4, 6).
  - **Sources**: [`src/core/include/`](../../src/core/include/).

- [x] **Task 2.3: Dual-Tier Rendering Backend**
  - **Empirical Result**: Tier 1 `HardwareCursorDriver` manages `64x64` canvases with an LRU cache of 64 master handles, verifying sub-55 us cached swap latency and handle duplication safety via `duplicate_cursor_for_system`. Tier 2 `CompanionOverlay` implements Mode A (Spawn-Anchored Toast) and Mode B (`RIDEV_INPUTSINK` follower) with `WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE` window styles verified in `tests/test_native_core.cpp` (Tests 3, 5).
  - **Sources**: [`src/core/include/hardware_driver.hpp`](../../src/core/include/hardware_driver.hpp), [`src/core/include/companion_overlay.hpp`](../../src/core/include/companion_overlay.hpp).

- [x] **Task 2.4: Standalone System Tray Daemon (`dynamic-cursor-tray.exe`)**
  - **Empirical Result**: Compact 96 KB native executable consuming 1.64 MB private working set during active named pipe streaming. Implements WinMain message pump, system tray notification icon (`Shell_NotifyIcon`), single-instance mutex (`Local\DynamicCursorSingleInstanceMutex`), and fail-safe recovery via `perform_system_restoration()` calling `SPI_SETCURSORS`. Verified in `tests/test_e2e_daemon.py::test_native_daemon_e2e`.
  - **Source**: [`src/daemon/main.cpp`](../../src/daemon/main.cpp).

- [x] **Task 2.5: Windhawk Mod Package (`dynamic-cursor.wh.cpp`)**
  - **Empirical Result**: Standalone Windhawk mod compiling under bundled Clang 20.1 targeting MinGW-w64 with `windhawk_api.h`. Detours `user32!SetCursor` using `Wh_SetFunctionHook` to maintain composite states inside applications that override system cursors during `WM_SETCURSOR`.
  - **Source & Documentation**: [`src/windhawk/dynamic-cursor.wh.cpp`](../../src/windhawk/dynamic-cursor.wh.cpp), [`src/windhawk/README.md`](../../src/windhawk/README.md).

- [x] **Task 2.6: Production Feeder Bridges**
  - **Empirical Result**: Implemented Python feeder SDK (`DynamicCursorClient` in `src/dynamic_cursor/client.py`), CLI subcommands (`python -m dynamic_cursor.client`), Caster voice engine integration (`examples/caster_mic_feeder.py`), and build task notifier (`examples/cli_notify.py`). All 8 end-to-end integration tests passed in `tests/test_e2e_daemon.py`.
  - **Sources**: [`src/dynamic_cursor/client.py`](../../src/dynamic_cursor/client.py), [`examples/`](../../examples/).

---

## 4. Phase 3: Current Horizons & Explorations

With the Phase 2 production native engine established, Phase 3 addresses procedural animation, display scaling, and expanded workspace feeders:

- **Task 3.1: Procedural Animated Sprite Frames**
  - Step through multi-frame sprite sheets or `.ani` memory streams inside the bottom-right quadrant of the `64x64` hardware cursor canvas.
  - Enable desktop companion sprites and animated state spinners without spawning overlay windows.
- **Task 3.2: Per-Monitor DPI Scaling Awareness**
  - Investigate `CreateIconIndirect` bitmap scaling behavior across mixed DPI setups (e.g., 100% and 150% displays).
  - Handle `WM_DPICHANGED` messages to scale the hardware canvas dynamically between `64x64`, `96x96`, and `128x128` pixels based on active monitor scaling metrics.
- **Task 3.3: Expanded Feeder Integrations**
  - Author reference feeder bridges for Active Desktop Context Engine, browser media playback status, and IDE background compilation watchers.
  - Provide minimal PowerShell and C# feeder client snippets.
