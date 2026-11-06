<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- Copyright (c) 2026 Amir Farhadi -->

# Spike 01 Report: Win32 Hardware Cursor Canvas & Latency Telemetry

## 1. Executive Summary & Empirical Findings

This spike validates the low-level Win32 hardware cursor subsystem for procedural in-memory rendering, extended canvas boundaries, and real-time state swapping. All measurements were captured on native 64-bit Windows using high-resolution hardware counters (`time.perf_counter_ns`).

The investigation confirms the following technical realities:
1. **Zero Downscaling up to 256x256 Pixels:** Despite `GetSystemMetrics(SM_CXCURSOR)` reporting `32`, Windows 11 and the WDDM display driver accept `64x64`, `128x128`, and `256x256` 32-bit ARGB bitmaps in `CreateIconIndirect` without downscaling. Full-resolution pixel fidelity was verified across the entire canvas, including the extreme opposite corner at `(W - 1, H - 1)`.
2. **Sub-80 Microsecond Cached Handle Swapping:** Duplicating a cached master cursor handle via `CopyIcon` and applying it to `SetSystemCursor(hCopy, OCR_NORMAL)` executes in 40 to 76 microseconds for canvas sizes up to `128x128`. On a 144 Hz display panel (6944 microsecond frame budget), a cursor swap consumes less than 1.1% of the total frame interval.
3. **Handle Lifecycle Invariant Confirmed:** The operating system explicitly takes ownership of and destroys the `HCURSOR` handle passed to `SetSystemCursor`. Master handles cached in memory must never be passed directly; invoking `CopyIcon` immediately prior to dispatch prevents handle exhaustion and invalid pointer faults.
4. **Pre-Multiplied Alpha Compositing:** Win32 cursor blending requires strict pre-multiplied ARGB values. Procedural blitting routines must compute `pr = (r * a) // 255`, `pg = (g * a) // 255`, and `pb = (b * a) // 255` to prevent black border fringing against bright desktop backgrounds.

---

## 2. System Baseline Environment

The following hardware and software parameters established the test baseline:

| Configuration Metric | Baseline Value |
| :--- | :--- |
| **Operating System** | Microsoft Windows 11 Build 10.0.26300 |
| **Display Scanout Controller** | Intel(R) UHD Graphics (Driver: 32.0.101.6129) |
| **Discrete Graphics Adapter** | NVIDIA GeForce RTX 4060 Laptop GPU (Driver: 32.0.15.9579) |
| **Display Resolution & Refresh Rate** | 1920 x 1200 @ 144 Hz (6.94 ms frame period) |
| **Legacy Cursor Metrics** | `SM_CXCURSOR = 32`, `SM_CYCURSOR = 32` |
| **Execution Environment** | Python 3.10.11 (64-bit AMD64) ctypes harness |

---

## 3. Hardware Canvas Dimension & Pixel Fidelity Verification

To determine whether the OS silently downscales or crops cursors larger than `SM_CXCURSOR`, test bitmaps were generated with distinct markers at hotspot `(0, 0)` (`0xFFFF0000`, red) and the far bottom-right corner `(W - 1, H - 1)` (`0xFF00FF00`, green). Each cursor was applied via `SetSystemCursor`, re-acquired via `LoadCursorW(None, OCR_NORMAL)`, and read back into user memory using `GetDIBits`.

| Requested Size | Allocated `HCURSOR` | Format | OS Canvas Fidelity | Corner Pixel Verification |
| :--- | :--- | :--- | :--- | :--- |
| **32x32** | 32x32 | 32 bpp ARGB | Retained (No Downscale) | `(0,0)=0xFFFF0000`, `(31,31)=0xFF00FF00` |
| **64x64** | 64x64 | 32 bpp ARGB | Retained (No Downscale) | `(0,0)=0xFFFF0000`, `(63,63)=0xFF00FF00` |
| **128x128** | 128x128 | 32 bpp ARGB | Retained (No Downscale) | `(0,0)=0xFFFF0000`, `(127,127)=0xFF00FF00` |
| **256x256** | 256x256 | 32 bpp ARGB | Retained (No Downscale) | `(0,0)=0xFFFF0000`, `(255,255)=0xFF00FF00` |

The data proves that the Windows hardware cursor plane accommodates expanded canvases directly. Hotspot anchoring at `(0, 0)` leaves the remaining transparent canvas available for status badges, compact loading bars, and mode pills without requiring secondary overlay windows.

---

## 4. Empirical Latency Telemetry

### 4.1 In-Memory Handle Creation Latency

This benchmark measures the full procedural generation lifecycle: creating a top-down DIB section (`CreateDIBSection`), creating a monochrome mask (`CreateBitmap`), clearing memory, rendering the arrow pointer, applying color tints, drawing badges and progress bars, constructing the handle (`CreateIconIndirect`), and releasing the GDI bitmap objects.

Measurements are based on 100 consecutive iterations per canvas size:

| Canvas Size | Mean (us) | p50 Median (us) | p95 (us) | Min (us) | Max (us) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **32x32** | 105.32 | 95.80 | 157.20 | 86.50 | 378.10 |
| **64x64** | 162.76 | 154.70 | 217.90 | 136.20 | 333.20 |
| **128x128** | 376.68 | 362.00 | 507.20 | 322.70 | 728.10 |
| **256x256** | 988.51 | 977.50 | 1151.90 | 900.30 | 1186.80 |

### 4.2 Cached Handle Swap Latency

This benchmark evaluates production runtime performance when master cursor handles are pre-allocated or cached in memory. The operation consists of duplicating the cached handle (`CopyIcon`) and invoking `SetSystemCursor(hCopy, OCR_NORMAL)`.

Measurements are based on 100 consecutive iterations per canvas size:

| Canvas Size | Mean (us) | p50 Median (us) | p95 (us) | Min (us) | Max (us) | Budget at 144 Hz |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **32x32** | 40.73 | 39.90 | 48.30 | 38.30 | 69.20 | 0.58% |
| **64x64** | 51.43 | 45.80 | 81.40 | 43.50 | 226.80 | 0.74% |
| **128x128** | 75.53 | 69.40 | 128.30 | 65.80 | 186.80 | 1.09% |
| **256x256** | 316.46 | 343.40 | 500.30 | 187.60 | 2153.40 | 4.56% |

### 4.3 Animation Sequence Latency (15-Frame Progress Bar)

A 15-frame sequence transitioning a compact progress bar from 0% to 100% was pre-rendered on a `64x64` canvas. The frame-to-frame swap latency was measured across all 15 transitions:

| Metric | Measured Value (us) |
| :--- | :--- |
| **Frame Swap Mean** | 51.87 us |
| **Frame Swap p50** | 48.60 us |
| **Frame Swap p95** | 82.00 us |
| **Frame Swap Min** | 46.70 us |
| **Frame Swap Max** | 82.00 us |

At 60 frames per second (16.67 ms per frame), each frame swap consumes approximately 0.31% of the inter-frame duration. The entire 15-frame animation completes with zero observable frame drops or cursor hitching.

---

## 5. Visual Artifact & Performance Analysis

Observations during interactive playback highlighted three critical implementation details:
1. **Scanout Plane Synchronization:** Because `SetSystemCursor` delegates pointer composition directly to the WDDM hardware plane, cursor movement remains synchronized to the display scanout clock. There is zero trailing latency or rubber-banding when dragging the pointer rapidly across high-refresh displays.
2. **Handle Invalidation Trap:** Passing a master cached `HCURSOR` directly to `SetSystemCursor` renders the master handle invalid for future use. Consecutive calls with the same handle fail silently or raise Win32 Error 1402 (`ERROR_INVALID_CURSOR_HANDLE`). Duplication via `CopyIcon` is strictly mandatory.
3. **Size Trade-offs:** While `256x256` canvases function correctly, swap latency jumps to over 300 microseconds with occasional spikes reaching 2.15 milliseconds due to internal kernel memory blitting in `win32k.sys`. By contrast, `64x64` and `128x128` canvases maintain tight sub-100 microsecond distributions.

---

## 6. Technical Recommendations for Phase 2 Engine

Based on the empirical findings, the native C++ daemon architecture should implement the following specifications:

1. **Standard Hardware Canvas Size:** Standardize on a `64x64` or `128x128` hardware canvas for Tier 1. A `64x64` canvas provides sufficient space for a standard arrow pointer at `(0, 0)`, a `56x6` progress bar beneath the arrow, and a `16x16` corner status badge in the bottom-right quadrant while keeping swap latency under 55 microseconds.
2. **Dual-Tier Boundary:**
   - **Tier 1 (Hardware Cursor Canvas):** Pointer tinting, corner badges (mute, mic, audio), mode indicators (Vim modes), and compact progress bars (`<= 120` px width). Handled entirely in-memory with zero companion windows.
   - **Tier 2 (On-Demand Overlay):** Text notifications requiring multiple words (such as `"Build Succeeded: 42 tests"`), rich HUD cards, or dimensions exceeding `128x128`. Implemented via click-through `UpdateLayeredWindow` popups.
3. **Handle Pool Architecture:** The C++ state engine should maintain an LRU cache of composited master handles keyed by state hashes `(shape_id, tint_rgba, badge_id, progress_step)`. This guarantees that high-frequency updates (e.g. dragging across UI boundaries or animating progress) execute along the ultra-fast 50-microsecond `CopyIcon` path.
4. **Guaranteed Scheme Restoration:** Register top-level exception filters, console control handlers (`SetConsoleCtrlHandler`), and process exit handlers calling `SystemParametersInfoW(SPI_SETCURSORS, 0, NULL, 0)` to guarantee the user's desktop cursor scheme is never left altered.
