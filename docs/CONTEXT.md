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
  - [System Architecture](architecture/01_system_architecture.md): Decoupled feeder-to-sink model, state priority compositor, and dual rendering paths.
  - [Win32 Subsystem Deep Dive](architecture/02_win32_subsystem_deep_dive.md): In-memory bitmap composition (`CreateIconIndirect`), hardware cursor plane size limits, and extended companion overlay mechanics.
- **Requirements**:
  - [Living Requirements](requirements/living_requirements.md): Functional and non-functional requirements, feeder integration stories, and acceptance criteria.
  - [Prior Art & Web Audit](requirements/prior_art_audit.md): Comparative analysis of existing open-source cursor changers, PowerToys overlays, and historical failure modes.
- **Roadmap & Explorations**:
  - [Living Roadmap](roadmap/living_roadmap.md): Near-term discovery spikes, upcoming tasks, and open architectural questions.
  - [Exploratory Vision](roadmap/exploratory_vision.md): Deep dive into C/C++ native packaging, Windhawk mod distribution, and procedural cursor capabilities.

---

## 3. Core Domain Ontology

```mermaid
classDiagram
    class ExternalFeeder {
        <<external producer>>
        +connect_pipe()
        +push_layer(layer_payload)
        +clear_layer(layer_id)
    }

    class LayerPayload {
        +string layer_id
        +int priority
        +string base_shape
        +uint32 tint_rgba
        +string badge_id
        +float progress_ratio
        +ToastSpec toast
    }

    class CursorStateCompositor {
        +Map~string, LayerPayload~ active_layers
        +upsert_layer(client_pid, payload)
        +drop_client_layers(client_pid)
        +evaluate_composite()
    }

    class HardwarePlaneDriver {
        +compose_argb_bitmap(base, tint, badge, progress) HCURSOR
        +apply_system_cursor(role, hcursor) bool
        +restore_defaults() void
    }

    class CompanionOverlayDriver {
        +show_ephemeral_hud(toast_spec, progress_spec) void
        +hide_hud() void
    }

    ExternalFeeder ..> LayerPayload : sends via Named Pipe
    CursorStateCompositor --> LayerPayload : arbitrates & merges
    CursorStateCompositor --> HardwarePlaneDriver : drives bitmap plane
    CursorStateCompositor --> CompanionOverlayDriver : drives extended HUD
```

### Domain Definitions
- **Cursor Sink (Actuator)**: The core `dynamic-cursor` process. It has zero knowledge of external applications and strictly renders the composite result of active state layers.
- **External Feeder (Producer)**: Any external process (Active Desktop Context Engine, Caster, CLI script) that connects to `\\.\pipe\dynamic_cursor` to assert or clear visual state layers.
- **Connection Lease**: Kernel-tracked lifetime binding between an external feeder's open pipe handle and its asserted cursor layers. If the feeder process crashes or exits, its layers are automatically purged.
- **Hardware Cursor Canvas**: A 32-bit pre-multiplied ARGB bitmap (`32x32` up to `128x128` pixels) created in memory via `CreateDIBSection` and `CreateIconIndirect`, rendered directly by the GPU hardware cursor plane with zero motion lag.
- **Extended Companion Area**: An on-demand, click-through Direct2D / Layered window (`WS_EX_LAYERED | WS_EX_TRANSPARENT`) spawned near the pointer only when visual elements exceed hardware cursor canvas dimensions (such as multi-word text notifications or expanded progress bars).

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
