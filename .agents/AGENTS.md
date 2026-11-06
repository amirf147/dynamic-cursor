<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- Copyright (c) 2026 Amir Farhadi -->

# Dynamic Cursor Workspace Rules

## Agent Constraints & Engineering Invariants

### Runtime & Language Stack
- **Target Architecture:** Native Windows C/C++ (C++20 / Win32 API: `user32.dll`, `gdi32.dll`, `d2d1.dll`, `dcomp.dll`) for the production standalone system tray daemon and optional Windhawk mod. Python 3.10+ (`ctypes`) is permitted for rapid empirical micro-spikes, test harnesses, and external feeder scripts.
- **Zero Heavyweight Dependencies:** The core cursor sink must compile with standard Win32 system libraries and zero external GUI frameworks.

### Architectural Invariants & Performance Boundaries
- **Pure Cursor Sink & Feeder Decoupling:** `dynamic-cursor` is a generalized visual actuator and compositor. It must not hardcode domain logic for external tools (such as Caster or workspace monitors). External producers (Caster, Active Desktop Context Engine, browser watchers, CLI tools) push state updates and visual payloads into `dynamic-cursor` over IPC (`\\.\pipe\dynamic_cursor`).
- **Zero Polling Invariant:** Never introduce polling loops (`while True: sleep(...)` or periodic `SetTimer` sweeps) for state detection or idle cursor tracking. All state triggers must be strictly event-driven (overlapped I/O for Named Pipes, kernel handle waits for client process leases, and on-demand `WM_INPUT` raw input sinks only while an extended overlay window is actively visible).
- **Handle Lifecycle & Memory Safety:**
  - `user32.SetSystemCursor` explicitly destroys the `HCURSOR` handle passed to it.
  - Master loaded or procedurally composited cursor handles (`CreateIconIndirect`) must remain cached in an internal registry and never passed directly to `SetSystemCursor`.
  - Every transition must duplicate the cached handle using `user32.CopyIcon` / `user32.CopyCursor` prior to calling `SetSystemCursor`.
- **Desktop Performance Purity:**
  - Avoid registry scheme swapping (`HKCU\Control Panel\Cursors`) paired with `SPIF_SENDCHANGE` during dynamic runtime switches. Broadcasting `WM_SETTINGCHANGE` forces every top-level window to reload settings, inducing visual stutter.
  - Runtime transitions for pointer shapes, color tints, and compact corner badges must execute in memory via `CreateIconIndirect` and `SetSystemCursor(CopyIcon(hCursor), OCR_NORMAL)`.
- **Fail-Safe System Restoration:**
  - Always register cleanup hooks (`atexit`, console control handlers, signal interceptors, and startup crash recovery) calling `user32.SystemParametersInfoW(0x0057, 0, None, 0)` (`SPI_SETCURSORS`) to restore the user's permanent Windows cursor scheme on exit.

### Documentation Philosophy & Living Specs
- **Living Documents:** All specifications, requirements, architecture models, and roadmaps are living documents. They reflect current understanding and must be updated alongside empirical micro-spikes and code evolution.
- **Documentation Hub:** [`docs/CONTEXT.md`](../docs/CONTEXT.md) serves as the canonical source of truth for domain ontology, design boundaries, and architecture specs.
- **Relative Links:** All internal markdown links must use clean relative paths.
- **Tone & Style:** Objective, technically grounded, pragmatic systems engineering tone. Clean active declarative sentences (2–4 per paragraph). No em dashes, no exclamation points, and no synthetic rule-of-three triads.

### Version Control & Safety Hygiene
- **Autonomous Commit Prohibition:** NEVER execute `git commit` or `git push` commands autonomously. Stage verified files (`git add`) and output a copy-paste ready conventional commit command for user review.
- **Path & Secret Hygiene:** Never commit hardcoded absolute user paths or credentials. Always run `python scripts/check_repo_safety.py` prior to presenting staged changes.
- **SPDX Licensing:** Every newly created source, configuration, and documentation file must include the standard Apache-2.0 SPDX header and copyright notice.
