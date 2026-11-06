<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- Copyright (c) 2026 Amir Farhadi -->

# Living Requirements Specification

## 1. Document Status & Purpose

This is a living requirements specification. Requirements, target capabilities, and acceptance criteria evolve as empirical discovery spikes clarify hardware cursor boundaries and rendering trade-offs.

---

## 2. Problem Statement

The operating system mouse pointer sits at the center of the user's visual focus during interactive desktop work, yet Windows treats the cursor as a static, isolated bitmap. Existing utilities that attempt to add status awareness or context to the cursor suffer from three core limitations:
1. **Tightly Coupled Monoliths**: Existing tools hardcode a single trigger source (such as a specific voice engine or a polling window watcher) directly into the cursor changer, preventing other tools from sharing the cursor.
2. **Stateless Overwrites**: Basic wrappers around `SetSystemCursor` lack priority arbitration or process leases. If two scripts modify the cursor, they overwrite each other and leave the OS pointer stuck if a script crashes.
3. **Overlay Lag vs. Small Bitmap Trade-off**: Pure hardware cursors are traditionally restricted to a small `32x32` area, while full-time DWM window overlays (such as PowerToys Mouse Utilities) rely on global `WH_MOUSE_LL` hooks that trail 1 to 2 frames behind hardware input and consume continuous CPU cycles.

Dynamic Cursor solves this by acting as a **generalized, minimal Cursor Compositor and State Sink**. Any external tool (Caster, an Active Desktop Context Engine, browser media watchers, or CLI scripts) can push visual state layers into the sink, which composites pointer shapes, color tints, corner badges, compact loading bars, and on-demand pop-out text notifications.

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

| ID | Requirement Title | Specification Details |
| :--- | :--- | :--- |
| **FR-1** | **In-Memory Hardware Compositing** | Generate and swap 32-bit ARGB system cursors (`OCR_NORMAL`, and optionally `OCR_IBEAM`, `OCR_HAND`) in memory using `CreateDIBSection`, `CreateIconIndirect`, and `SetSystemCursor`. |
| **FR-2** | **Universal Named Pipe Sink** | Expose an asynchronous Named Pipe endpoint (`\\.\pipe\dynamic_cursor`) accepting structured layer payloads (`base_shape`, `tint_rgba`, `corner_badge`, `progress_bar`, `toast`). |
| **FR-3** | **Multi-Channel Priority Arbitration** | Maintain a priority table of active layers and composite highest-priority orthogonal visual channels into a unified output state. |
| **FR-4** | **Kernel-Backed Client Leases** | Bind active layers to the feeder's pipe connection handle (or explicit `ttl_ms`) so crashed or disconnected feeders have their states automatically purged. |
| **FR-5** | **Two-Tier Area Extension** | Render compact badges, status dots, and mini progress bars inside an expanded ARGB hardware cursor canvas (Tier 1), and route wider text notifications or HUD cards to an on-demand click-through companion window (Tier 2). |
| **FR-6** | **Fail-Safe OS Restoration** | Execute `SystemParametersInfoW(SPI_SETCURSORS, 0, None, 0)` upon process exit, `SIGINT`, `SIGTERM`, and startup recovery after abnormal termination. |

---

## 5. Non-Functional Requirements (NFR)

| ID | Requirement Title | Target Metric & Constraint |
| :--- | :--- | :--- |
| **NFR-1** | **Hardware Switch Latency** | Tier 1 in-memory cursor composition and `SetSystemCursor` swap must complete in under 0.5 ms. |
| **NFR-2** | **Zero Idle CPU Usage** | CPU utilization must remain at 0.0% when no state transitions or ephemeral animations are active. Global `WH_MOUSE_LL` hooks are prohibited. |
| **NFR-3** | **Zero Display Stutter** | Runtime transitions must never broadcast `WM_SETTINGCHANGE` or mutate `HKCU\Control Panel\Cursors`. |
| **NFR-4** | **Minimal Native Footprint** | Production C/C++ daemon target must operate under 2 MB RAM working set with zero heavyweight GUI framework dependencies. |
| **NFR-5** | **Strict Feeder Decoupling** | Core repository code must contain zero runtime dependencies on Caster or specific external workspace monitors. |

---

## 6. Acceptance Criteria

1. **Multi-Feeder Composition Test**: Two separate processes pushing different channels (e.g., Process A pushes a color tint at priority 80; Process B pushes a corner badge at priority 20) result in a single composited cursor displaying both properties.
2. **Feeder Crash Lease Test**: Forcibly killing a connected feeder process (`Stop-Process -Force`) immediately removes its visual layer from the cursor with zero manual intervention.
3. **Extended Notification Click-Through Test**: Popping out a text notification or loading bar near the cursor never steals keyboard focus (`WS_EX_NOACTIVATE`) and allows mouse clicks to pass unimpeded to the underlying window (`WS_EX_TRANSPARENT`).
4. **Daemon Exit Recovery Test**: Terminating the cursor daemon restores the user's permanent Windows cursor scheme cleanly via `SPI_SETCURSORS`.
