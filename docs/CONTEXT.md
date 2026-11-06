<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- Copyright (c) 2026 Amir Farhadi -->

# Dynamic Cursor: Context & Domain Model

## 1. System Mission & Scope

Dynamic Cursor is a generalized, event-driven Windows pointer compositor and visual state sink. Its primary responsibility is to dynamically modify mouse pointer shapes, sizes, color tints, attached corner badges, loading indicators, and micro-notifications in real time based on external commands, without relying on periodic polling loops or inducing display stutter.

Rather than embedding workspace observation logic inside the cursor tool, Dynamic Cursor operates as a minimal, standalone actuator listening over local IPC (`\\.\pipe\dynamic_cursor`). Any external producer (such as an Active Desktop Context Engine, Caster voice recognition, browser media watchers, build scripts, or accessibility utilities) can push visual state layers to the cursor in real time.

---

## 2. Living Documentation Charter

All documents in this repository are living documents. Technical assumptions, requirement specifications, architectural boundaries, and implementation roadmaps are intentionally kept adaptable so empirical micro-spikes can guide downstream architecture choices.

### Documentation Index
- **Architecture**:
  - [System Architecture](architecture/01_system_architecture.md): Decoupled feeder-to-sink model, state priority compositor, dual rendering paths, and native C++20 dual-target architecture.
  - [Win32 Subsystem Deep Dive](architecture/02_win32_subsystem_deep_dive.md): In-memory bitmap composition (`CreateIconIndirect`), hardware cursor plane size limits, companion overlay mechanics (Mode A and Mode B), and `user32!SetCursor` interception.
  - [IPC Wire Protocol & Feeder SDK](architecture/03_ipc_wire_protocol_and_sdk.md): Length-prefixed framing, packed 32-byte binary struct, JSON schema, kernel lease lifecycle, and Python feeder SDK reference.
- **Empirical Spikes & Telemetry**:
  - [Spike 01: Hardware Cursor Canvas & Latency](spikes/report_01_hardware_canvas.md): Validates `64x64` / `128x128` hardware canvas fidelity, sub-55 us cached swap latency, and 15-frame progress bar animation.
  - [Spike 02: Companion Overlay Architecture](spikes/report_02_companion_overlay.md): Evaluates Mode A (Spawn-Anchored Toast, 0.35 ms median spawn) vs Mode B (Raw Input Follower, 74 us repositioning) and proves focus preservation and click-through invariants.
  - [Spike 03: Named Pipe Leases & State Compositor](spikes/report_03_named_pipe_leases.md): Benchmarks length-prefixed IPC throughput (21,768 msg/s, 25 us latency) and proves 940 us kernel lease rollback on process crash.
  - [Spike 04: Native C++20 Toolchain & Windhawk](spikes/report_04_native_toolchain.md): Host compiler discovery (MSVC 19.50, WinSDK 10.0.26100, Windhawk Clang 20.1), binary footprint (256 KB .exe, 1.33 MB private working set), and Windhawk mod interception mapping.
- **Requirements**:
  - [Living Requirements](requirements/living_requirements.md): Functional and non-functional requirements, feeder integration stories, and acceptance criteria.
  - [Prior Art & Web Audit](requirements/prior_art_audit.md): Comparative analysis of existing open-source cursor changers, PowerToys overlays, and historical failure modes.
- **Roadmap & Explorations**:
  - [Living Roadmap](roadmap/living_roadmap.md): Phase 1 empirical completion metrics, Phase 2 native C++20 implementation tasks, and Phase 3 exploration horizons.
  - [Exploratory Vision](roadmap/exploratory_vision.md): Deep dive into C/C++ native packaging, Windhawk mod distribution, and procedural cursor capabilities.

---

## 3. Core Domain Ontology

```mermaid
classDiagram
    class DynamicCursorClient {
        <<python feeder sdk>>
        +connect()
        +set_layer(layer_id, priority, ...)
        +clear_layer(layer_id)
        +clear_all()
        +show_toast(text, duration_ms)
    }

    class IpcServer {
        <<native c++20>>
        -wstring pipe_name_
        -CursorStateCompositor* compositor_
        +start() bool
        +stop() void
        +active_clients_count() size_t
    }

    class LayerPayload {
        <<native c++20>>
        +string layer_id
        +int32 priority
        +BaseShape shape
        +uint32 tint_argb
        +BadgeId badge
        +float progress
        +ToastSpec toast
        +uint64 client_session_id
    }

    class CursorStateCompositor {
        <<native c++20>>
        +upsert_layer(session_id, payload)
        +remove_layer(session_id, layer_id)
        +drop_client_layers(session_id)
        +purge_expired_layers(now_tick) CompositeState
        +evaluate_composite() CompositeState
    }

    class BitmapCompositor {
        <<native c++20>>
        +compose(state, canvas_size) UniqueCursor
    }

    class HardwareCursorDriver {
        <<native c++20>>
        -LRUCache master_cache_
        +apply_state(state, dry_run) bool
        +restore_system_default() void
    }

    class CompanionOverlay {
        <<native c++20>>
        -HWND hwnd_
        +show_toast(text, duration_ms)
        +start_following()
        +stop_following()
    }

    DynamicCursorClient ..> IpcServer : transmits length-prefixed frames
    IpcServer --> CursorStateCompositor : routes decoded LayerPayload
    CursorStateCompositor --> HardwareCursorDriver : sends arbitrated CompositeState
    CursorStateCompositor --> CompanionOverlay : triggers extended HUD / Toast
    HardwareCursorDriver --> BitmapCompositor : requests composed ARGB bitmaps
```

### Production Component Mapping

The production C++20 core and Python feeder SDK map to the following source units:

| Component Name | Source Location | Primary Role & Invariants |
| :--- | :--- | :--- |
| **`protocol.hpp`** | [`src/core/include/protocol.hpp`](../src/core/include/protocol.hpp) | Defines `BinaryPacket` (32 bytes), `LayerPayload`, `ToastSpec`, `BaseShape`, `BadgeId`, `Operation`, and internal JSON/color parsers. |
| **`state_compositor.hpp`** | [`src/core/include/state_compositor.hpp`](../src/core/include/state_compositor.hpp) | Implements `CursorStateCompositor` and `CompositeState`. Arbitrates orthogonal channels and tracks TTL expirations. |
| **`bitmap_compositor.hpp`** | [`src/core/include/bitmap_compositor.hpp`](../src/core/include/bitmap_compositor.hpp) | Implements procedural 32-bit ARGB rasterization (`premultiply_argb`, DIB pixel arrays, and `CreateIconIndirect`). |
| **`hardware_driver.hpp`** | [`src/core/include/hardware_driver.hpp`](../src/core/include/hardware_driver.hpp) | Implements `HardwareCursorDriver` with an LRU handle cache (capacity 64), `CopyIcon` duplication, and `SetSystemCursor(OCR_NORMAL)`. |
| **`companion_overlay.hpp`** | [`src/core/include/companion_overlay.hpp`](../src/core/include/companion_overlay.hpp) | Implements `CompanionOverlay` managing Mode A (Spawn-Anchored Toast) and Mode B (`RIDEV_INPUTSINK` follower). |
| **`ipc_server.hpp`** | [`src/core/include/ipc_server.hpp`](../src/core/include/ipc_server.hpp) | Implements asynchronous overlapped Named Pipe server, kernel session lease tracking, and automatic rollback on `ERROR_BROKEN_PIPE`. |
| **`raii_handles.hpp`** | [`src/core/include/raii_handles.hpp`](../src/core/include/raii_handles.hpp) | Provides RAII wrappers (`UniqueCursor`, `UniqueBitmap`, `UniqueDc`, `UniqueHandle`) and `duplicate_cursor_for_system`. |
| **Standalone Tray Daemon** | [`src/daemon/main.cpp`](../src/daemon/main.cpp) | WinMain message pump, system tray notification icon (`Shell_NotifyIcon`), single-instance mutex, and fail-safe recovery. |
| **Windhawk Mod** | [`src/windhawk/dynamic-cursor.wh.cpp`](../src/windhawk/dynamic-cursor.wh.cpp) | Per-process hook detouring `user32!SetCursor` to intercept applications that override system cursor slots. |
| **Python Feeder SDK** | [`src/dynamic_cursor/client.py`](../src/dynamic_cursor/client.py) | Win32 Named Pipe client (`DynamicCursorClient`) and command-line interface (`python -m dynamic_cursor.client`). |

### Domain Definitions
- **Cursor Sink (Actuator)**: The core `dynamic-cursor` daemon (`dynamic-cursor-tray.exe`). It has zero knowledge of external applications and strictly renders the composite result of active state layers.
- **External Feeder (Producer)**: Any external process (Active Desktop Context Engine, Caster, CLI script) that connects to `\\.\pipe\dynamic_cursor` to assert or clear visual state layers.
- **Connection Lease**: Kernel-tracked lifetime binding between an external feeder's open pipe handle and its asserted cursor layers. If the feeder process crashes or exits, the Windows Object Manager closes the handle table, signaling `ERROR_BROKEN_PIPE` in under 1.0 ms (empirically 940 us) to automatically purge all associated layers.
- **Hardware Cursor Canvas**: A 32-bit pre-multiplied ARGB bitmap standardized at `64x64` pixels (and expandable to `128x128`) created in memory via `CreateDIBSection` and `CreateIconIndirect`. Rendered directly by the GPU hardware cursor plane with zero motion lag and sub-55 us cached swap latency.
- **Extended Companion Area**: An on-demand, click-through Layered window (`WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE`) spawned near the pointer only when visual elements exceed hardware cursor canvas dimensions (such as multi-word text notifications or expanded progress bars). Operates via Mode A (Spawn-Anchored Toast, 0 active hooks, 0.0% CPU when holding) or Mode B (On-Demand Raw Input Follower, 74 us repositioning, 0 lingering hooks).

---

## 4. Engineering Invariants & Boundaries

1. **Zero Polling Invariant**:
   Polling loops (`while True: sleep(...)` or periodic timers) are strictly prohibited. All transitions are triggered by incoming IPC packets or OS kernel wait handles.
2. **In-Memory Hardware Swapping**:
   Dynamic runtime transitions for shapes, tints, and corner badges execute in memory via `CreateIconIndirect` and `user32.SetSystemCursor`. Registry modification paired with `SPIF_SENDCHANGE` is forbidden during runtime switches.
3. **Handle Duplication Protocol**:
   Because `SetSystemCursor` destroys the handle passed to it, the engine never passes a cached master handle directly. It always passes a duplicate created via `CopyIcon`.
4. **On-Demand Overlay Lifecycle**:
   When the Extended Companion Area is inactive, zero mouse-tracking hooks (`WM_INPUT` or WinEvents) may remain registered.
5. **Guaranteed Restoration**:
   On process exit, crash interception, or unload, the engine executes `SystemParametersInfoW(SPI_SETCURSORS, 0, None, 0)` to restore the operating system default cursor scheme.
