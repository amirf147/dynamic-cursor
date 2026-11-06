# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Amir Farhadi
"""
Spike 01: Win32 Hardware Cursor Canvas & Latency Benchmark

Empirically benchmarks:
1. Procedural in-memory 32-bit ARGB cursor generation via CreateDIBSection + CreateIconIndirect.
2. Hardware cursor canvas dimensions (32x32, 64x64, 128x128, 256x256).
3. Handle swap latency (CopyIcon + SetSystemCursor) across canvas dimensions.
4. 15-frame animated progress bar transition latency.
5. Verification of bitmap scaling and corner pixel fidelity.
"""

from __future__ import annotations

import argparse
import atexit
import ctypes
from ctypes import POINTER, Structure, byref, c_int32, c_uint16, c_uint32, c_void_p
import json
import platform
import statistics
import subprocess
import sys
import time
from typing import Any, Dict, List, Tuple

# Win32 Constants
OCR_NORMAL = 32512
SPI_SETCURSORS = 0x0057
BI_RGB = 0
DIB_RGB_COLORS = 0
SM_CXCURSOR = 13
SM_CYCURSOR = 14

# Win32 Structures (64-bit safe: handles as c_void_p)
class BITMAPINFOHEADER(Structure):
    _fields_ = [
        ("biSize", c_uint32),
        ("biWidth", c_int32),
        ("biHeight", c_int32),
        ("biPlanes", c_uint16),
        ("biBitCount", c_uint16),
        ("biCompression", c_uint32),
        ("biSizeImage", c_uint32),
        ("biXPelsPerMeter", c_int32),
        ("biYPelsPerMeter", c_int32),
        ("biClrUsed", c_uint32),
        ("biClrImportant", c_uint32),
    ]

class ICONINFO(Structure):
    _fields_ = [
        ("fIcon", c_int32),
        ("xHotspot", c_uint32),
        ("yHotspot", c_uint32),
        ("hbmMask", c_void_p),
        ("hbmColor", c_void_p),
    ]

class BITMAP(Structure):
    _fields_ = [
        ("bmType", c_int32),
        ("bmWidth", c_int32),
        ("bmHeight", c_int32),
        ("bmWidthBytes", c_int32),
        ("bmPlanes", c_uint16),
        ("bmBitsPixel", c_uint16),
        ("bmBits", c_void_p),
    ]

# Win32 Function Bindings with Explicit 64-bit Types
user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

user32.SystemParametersInfoW.argtypes = [c_uint32, c_uint32, c_void_p, c_uint32]
user32.SystemParametersInfoW.restype = c_int32

user32.GetSystemMetrics.argtypes = [c_int32]
user32.GetSystemMetrics.restype = c_int32

user32.CreateIconIndirect.argtypes = [POINTER(ICONINFO)]
user32.CreateIconIndirect.restype = c_void_p

user32.DestroyIcon.argtypes = [c_void_p]
user32.DestroyIcon.restype = c_int32

user32.CopyIcon.argtypes = [c_void_p]
user32.CopyIcon.restype = c_void_p

user32.SetSystemCursor.argtypes = [c_void_p, c_uint32]
user32.SetSystemCursor.restype = c_int32

user32.GetIconInfo.argtypes = [c_void_p, POINTER(ICONINFO)]
user32.GetIconInfo.restype = c_int32

user32.LoadCursorW.argtypes = [c_void_p, c_void_p]
user32.LoadCursorW.restype = c_void_p

user32.GetDC.argtypes = [c_void_p]
user32.GetDC.restype = c_void_p

user32.ReleaseDC.argtypes = [c_void_p, c_void_p]
user32.ReleaseDC.restype = c_int32

gdi32.CreateDIBSection.argtypes = [
    c_void_p,
    POINTER(BITMAPINFOHEADER),
    c_uint32,
    POINTER(c_void_p),
    c_void_p,
    c_uint32,
]
gdi32.CreateDIBSection.restype = c_void_p

gdi32.CreateBitmap.argtypes = [c_int32, c_int32, c_uint32, c_uint32, c_void_p]
gdi32.CreateBitmap.restype = c_void_p

gdi32.DeleteObject.argtypes = [c_void_p]
gdi32.DeleteObject.restype = c_int32

gdi32.GetObjectW.argtypes = [c_void_p, c_int32, c_void_p]
gdi32.GetObjectW.restype = c_int32

gdi32.GetDIBits.argtypes = [
    c_void_p,
    c_void_p,
    c_uint32,
    c_uint32,
    c_void_p,
    POINTER(BITMAPINFOHEADER),
    c_uint32,
]
gdi32.GetDIBits.restype = c_int32


def restore_system_cursor() -> None:
    """Restores the standard Windows cursor scheme via SPI_SETCURSORS."""
    user32.SystemParametersInfoW(SPI_SETCURSORS, 0, None, 0)


atexit.register(restore_system_cursor)


def premultiply_argb(a: int, r: int, g: int, b: int) -> int:
    """Calculates 32-bit pre-multiplied ARGB pixel value."""
    alpha = a & 0xFF
    pr = ((r & 0xFF) * alpha) // 255
    pg = ((g & 0xFF) * alpha) // 255
    pb = ((b & 0xFF) * alpha) // 255
    return (alpha << 24) | (pr << 16) | (pg << 8) | pb


ARROW_MASK: List[str] = [
    "X               ",
    "XX              ",
    "X.X             ",
    "X..X            ",
    "X...X           ",
    "X....X          ",
    "X.....X         ",
    "X......X        ",
    "X.......X       ",
    "X........X      ",
    "X.........X     ",
    "X.....XXXXXX    ",
    "X..X..X         ",
    "X.X X..X        ",
    "XX   X..X       ",
    "X    X..X       ",
    "      X..X      ",
    "      X..X      ",
    "       XX       ",
]


def draw_arrow_pointer(
    pixel_arr: Any,
    width: int,
    height: int,
    tint_argb: int,
    border_argb: int = 0xFF000000,
) -> None:
    """Renders the base pointer at hotspot (0, 0) into the pre-multiplied pixel buffer."""
    for y, row in enumerate(ARROW_MASK):
        if y >= height:
            break
        for x, ch in enumerate(row):
            if x >= width:
                break
            if ch == "X":
                pixel_arr[y * width + x] = border_argb
            elif ch == ".":
                pixel_arr[y * width + x] = tint_argb


def draw_corner_badge(
    pixel_arr: Any,
    width: int,
    height: int,
    fill_argb: int,
    border_argb: int = 0xFF000000,
) -> None:
    """Renders a status indicator badge in the bottom-right quadrant of the canvas."""
    margin = max(2, width // 16)
    radius = max(3, width // 8)
    cx = width - margin - radius
    cy = height - margin - radius

    r_outer_sq = radius * radius
    r_inner_sq = max(0.0, (radius - 1.4) ** 2)

    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            px = cx + dx
            py = cy + dy
            if 0 <= px < width and 0 <= py < height:
                d2 = dx * dx + dy * dy
                if d2 <= r_outer_sq:
                    if d2 >= r_inner_sq:
                        pixel_arr[py * width + px] = border_argb
                    else:
                        pixel_arr[py * width + px] = fill_argb


def draw_progress_bar(
    pixel_arr: Any,
    width: int,
    height: int,
    progress: float,
    fill_argb: int = 0xFF00E5FF,
    bg_argb: int = 0x80202020,
    border_argb: int = 0xFF000000,
) -> None:
    """Renders a compact progress bar beneath the arrow pointer."""
    x0 = 1
    y0 = 22
    bar_h = 6
    bar_w = min(width - 2, 56 if width <= 64 else min(width - 8, 120))

    if y0 + bar_h > height:
        return

    progress_clamped = max(0.0, min(1.0, progress))
    fill_w = int((bar_w - 2) * progress_clamped)

    for dy in range(bar_h):
        for dx in range(bar_w):
            px = x0 + dx
            py = y0 + dy
            if 0 <= px < width and 0 <= py < height:
                if dx == 0 or dx == bar_w - 1 or dy == 0 or dy == bar_h - 1:
                    pixel_arr[py * width + px] = border_argb
                elif (dx - 1) < fill_w:
                    pixel_arr[py * width + px] = fill_argb
                else:
                    pixel_arr[py * width + px] = bg_argb


def create_procedural_cursor(
    width: int,
    height: int,
    tint_argb: int = 0xFFFFFFFF,
    badge_argb: int | None = None,
    progress: float | None = None,
    progress_fill_argb: int = 0xFF00E5FF,
) -> int:
    """Constructs a 32-bit ARGB HCURSOR in memory."""
    bmi = BITMAPINFOHEADER()
    bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.biWidth = width
    bmi.biHeight = -height
    bmi.biPlanes = 1
    bmi.biBitCount = 32
    bmi.biCompression = BI_RGB

    bits = c_void_p()
    hbm_color = gdi32.CreateDIBSection(
        None, byref(bmi), DIB_RGB_COLORS, byref(bits), None, 0
    )
    if not hbm_color or not bits.value:
        raise RuntimeError(f"CreateDIBSection failed for size {width}x{height}")

    hbm_mask = gdi32.CreateBitmap(width, height, 1, 1, None)
    if not hbm_mask:
        gdi32.DeleteObject(hbm_color)
        raise RuntimeError(f"CreateBitmap mask failed for size {width}x{height}")

    try:
        pixel_arr = (c_uint32 * (width * height)).from_address(bits.value)
        # Clear buffer to fully transparent
        ctypes.memset(bits.value, 0, width * height * 4)

        draw_arrow_pointer(pixel_arr, width, height, tint_argb)

        if badge_argb is not None:
            draw_corner_badge(pixel_arr, width, height, badge_argb)

        if progress is not None:
            draw_progress_bar(
                pixel_arr, width, height, progress, fill_argb=progress_fill_argb
            )

        ii = ICONINFO()
        ii.fIcon = 0
        ii.xHotspot = 0
        ii.yHotspot = 0
        ii.hbmMask = hbm_mask
        ii.hbmColor = hbm_color

        h_cursor = user32.CreateIconIndirect(byref(ii))
        if not h_cursor:
            raise RuntimeError(f"CreateIconIndirect failed for size {width}x{height}")
        return h_cursor
    finally:
        gdi32.DeleteObject(hbm_color)
        gdi32.DeleteObject(hbm_mask)


def inspect_cursor_dimensions(h_cursor: int) -> Tuple[int, int, int]:
    """Inspects the internal bitmap dimensions and bits-per-pixel of an HCURSOR."""
    ii = ICONINFO()
    if not user32.GetIconInfo(c_void_p(h_cursor), byref(ii)):
        raise RuntimeError("GetIconInfo failed on cursor")

    try:
        bmp = BITMAP()
        gdi32.GetObjectW(c_void_p(ii.hbmColor), ctypes.sizeof(BITMAP), byref(bmp))
        return bmp.bmWidth, bmp.bmHeight, bmp.bmBitsPixel
    finally:
        if ii.hbmColor:
            gdi32.DeleteObject(c_void_p(ii.hbmColor))
        if ii.hbmMask:
            gdi32.DeleteObject(c_void_p(ii.hbmMask))


def query_system_baseline() -> Dict[str, Any]:
    """Queries hardware baseline: OS version, cursor metrics, and display adapter."""
    sm_cx = user32.GetSystemMetrics(SM_CXCURSOR)
    sm_cy = user32.GetSystemMetrics(SM_CYCURSOR)

    display_gpu = "Unknown"
    render_gpu = "Unknown"
    driver_version = "Unknown"
    refresh_rate = "Unknown"
    res_str = "Unknown"

    try:
        cmd = (
            "powershell -Command \"Get-CimInstance Win32_VideoController | "
            "Select-Object Name, DriverVersion, VideoModeDescription, CurrentRefreshRate | "
            "ConvertTo-Json\""
        )
        res = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if res.returncode == 0 and res.stdout.strip():
            raw = json.loads(res.stdout)
            controllers = raw if isinstance(raw, list) else [raw]
            for ctrl in controllers:
                name = ctrl.get("Name", "")
                mode = ctrl.get("VideoModeDescription")
                rate = ctrl.get("CurrentRefreshRate")
                d_ver = ctrl.get("DriverVersion", "")
                if mode or rate:
                    display_gpu = name
                    driver_version = d_ver
                    refresh_rate = f"{rate} Hz" if rate else "Unknown"
                    res_str = str(mode)
                else:
                    render_gpu = name
    except Exception:
        pass

    return {
        "os_platform": platform.platform(),
        "python_version": platform.python_version(),
        "sm_cxcursor": sm_cx,
        "sm_cycursor": sm_cy,
        "display_adapter": display_gpu,
        "display_driver_version": driver_version,
        "discrete_adapter": render_gpu,
        "display_resolution": res_str,
        "refresh_rate": refresh_rate,
    }


def compute_statistics(samples_us: List[float]) -> Dict[str, float]:
    """Calculates latency statistics in microseconds."""
    sorted_samples = sorted(samples_us)
    n = len(sorted_samples)
    idx_50 = int(n * 0.50)
    idx_95 = int(n * 0.95)
    return {
        "mean": statistics.mean(sorted_samples),
        "p50": sorted_samples[idx_50],
        "p95": sorted_samples[idx_95],
        "min": sorted_samples[0],
        "max": sorted_samples[-1],
    }


def benchmark_creation_latency(
    sizes: List[int], iterations: int = 100
) -> Dict[int, Dict[str, float]]:
    """Measures in-memory cursor creation latency across sizes."""
    results: Dict[int, Dict[str, float]] = {}

    for size in sizes:
        samples: List[float] = []
        for _ in range(iterations):
            t0 = time.perf_counter_ns()
            h_cursor = create_procedural_cursor(
                width=size,
                height=size,
                tint_argb=0xFF00C853,
                badge_argb=0xFFD50000,
                progress=0.5,
            )
            t1 = time.perf_counter_ns()
            user32.DestroyIcon(c_void_p(h_cursor))
            samples.append((t1 - t0) / 1000.0)
        results[size] = compute_statistics(samples)

    return results


def benchmark_swap_latency(
    sizes: List[int], iterations: int = 100
) -> Dict[int, Dict[str, float]]:
    """Measures cached handle duplicate and swap latency across sizes."""
    results: Dict[int, Dict[str, float]] = {}

    try:
        for size in sizes:
            h_master = create_procedural_cursor(
                width=size,
                height=size,
                tint_argb=0xFF00C853,
                badge_argb=0xFFD50000,
                progress=0.75,
            )
            samples: List[float] = []
            for _ in range(iterations):
                t0 = time.perf_counter_ns()
                h_copy = user32.CopyIcon(c_void_p(h_master))
                user32.SetSystemCursor(c_void_p(h_copy), OCR_NORMAL)
                t1 = time.perf_counter_ns()
                samples.append((t1 - t0) / 1000.0)

            user32.DestroyIcon(c_void_p(h_master))
            results[size] = compute_statistics(samples)
    finally:
        restore_system_cursor()

    return results


def benchmark_progress_animation_latency(
    size: int = 64, frames: int = 15
) -> Tuple[List[float], Dict[str, float]]:
    """Measures frame-to-frame transition latency for an animated progress sequence."""
    frame_handles: List[int] = []
    for f in range(frames):
        p = f / float(frames - 1)
        h = create_procedural_cursor(
            width=size,
            height=size,
            tint_argb=0xFFFFFFFF,
            progress=p,
            progress_fill_argb=0xFF00E5FF,
        )
        frame_handles.append(h)

    samples: List[float] = []
    try:
        for h_master in frame_handles:
            t0 = time.perf_counter_ns()
            h_copy = user32.CopyIcon(c_void_p(h_master))
            user32.SetSystemCursor(c_void_p(h_copy), OCR_NORMAL)
            t1 = time.perf_counter_ns()
            samples.append((t1 - t0) / 1000.0)
    finally:
        restore_system_cursor()
        for h in frame_handles:
            user32.DestroyIcon(c_void_p(h))

    stats = compute_statistics(samples)
    return samples, stats


def verify_corner_pixel_fidelity(
    size: int,
) -> Tuple[bool, str, str]:
    """Verifies that SetSystemCursor retains full resolution and does not downscale."""
    bmi = BITMAPINFOHEADER()
    bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.biWidth = size
    bmi.biHeight = -size
    bmi.biPlanes = 1
    bmi.biBitCount = 32
    bmi.biCompression = BI_RGB

    bits = c_void_p()
    hbm_color = gdi32.CreateDIBSection(
        None, byref(bmi), DIB_RGB_COLORS, byref(bits), None, 0
    )
    hbm_mask = gdi32.CreateBitmap(size, size, 1, 1, None)

    pixel_arr = (c_uint32 * (size * size)).from_address(bits.value)
    ctypes.memset(bits.value, 0, size * size * 4)

    expected_00 = 0xFFFF0000
    expected_corner = 0xFF00FF00
    pixel_arr[0] = expected_00
    pixel_arr[size * size - 1] = expected_corner

    ii = ICONINFO()
    ii.fIcon = 0
    ii.xHotspot = 0
    ii.yHotspot = 0
    ii.hbmMask = hbm_mask
    ii.hbmColor = hbm_color

    h_master = user32.CreateIconIndirect(byref(ii))
    gdi32.DeleteObject(hbm_color)
    gdi32.DeleteObject(hbm_mask)

    try:
        h_copy = user32.CopyIcon(c_void_p(h_master))
        user32.SetSystemCursor(c_void_p(h_copy), OCR_NORMAL)

        h_cur = user32.LoadCursorW(None, c_void_p(OCR_NORMAL))
        cur_ii = ICONINFO()
        user32.GetIconInfo(h_cur, byref(cur_ii))

        hdc = user32.GetDC(None)
        try:
            read_bmi = BITMAPINFOHEADER()
            read_bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            read_bmi.biWidth = size
            read_bmi.biHeight = -size
            read_bmi.biPlanes = 1
            read_bmi.biBitCount = 32
            read_bmi.biCompression = BI_RGB

            out_pixels = (c_uint32 * (size * size))()
            gdi32.GetDIBits(
                hdc,
                c_void_p(cur_ii.hbmColor),
                0,
                size,
                byref(out_pixels),
                byref(read_bmi),
                DIB_RGB_COLORS,
            )

            actual_00 = out_pixels[0]
            actual_corner = out_pixels[size * size - 1]
            match_pass = (actual_00 == expected_00) and (
                actual_corner == expected_corner
            )
            return match_pass, hex(actual_00), hex(actual_corner)
        finally:
            user32.ReleaseDC(None, hdc)
            if cur_ii.hbmColor:
                gdi32.DeleteObject(c_void_p(cur_ii.hbmColor))
            if cur_ii.hbmMask:
                gdi32.DeleteObject(c_void_p(cur_ii.hbmMask))
    finally:
        user32.DestroyIcon(c_void_p(h_master))
        restore_system_cursor()


def run_visual_demonstration() -> None:
    """Executes a short interactive visual sequence across shapes, tints, and animations."""
    print("\n--- Starting Visual Demonstration ---")
    sizes = [32, 64, 128]
    tints = [
        ("White Default", 0xFFFFFFFF, None),
        ("Green Accent", 0xFF00C853, 0xFF00E676),
        ("Red Alert", 0xFFD50000, 0xFFFF1744),
        ("Amber Warning", 0xFFFFAB00, 0xFFFFD600),
    ]

    try:
        for size in sizes:
            for name, tint, badge in tints:
                print(f"Displaying {size}x{size} canvas with {name}...")
                h = create_procedural_cursor(
                    width=size,
                    height=size,
                    tint_argb=tint,
                    badge_argb=badge,
                    progress=0.6,
                )
                h_copy = user32.CopyIcon(c_void_p(h))
                user32.SetSystemCursor(c_void_p(h_copy), OCR_NORMAL)
                time.sleep(0.3)
                user32.DestroyIcon(c_void_p(h))

        print("Streaming 15-frame progress bar animation at 60 Hz...")
        frames = 15
        handles = [
            create_procedural_cursor(
                width=64,
                height=64,
                tint_argb=0xFFFFFFFF,
                progress=f / float(frames - 1),
            )
            for f in range(frames)
        ]
        for h in handles:
            h_copy = user32.CopyIcon(c_void_p(h))
            user32.SetSystemCursor(c_void_p(h_copy), OCR_NORMAL)
            time.sleep(1.0 / 60.0)
        for h in handles:
            user32.DestroyIcon(c_void_p(h))

    finally:
        restore_system_cursor()
        print("Cursor scheme cleanly restored.")


def print_table(headers: List[str], rows: List[List[str]]) -> None:
    """Formats and prints an ASCII alignment table."""
    widths = [len(h) for h in headers]
    for row in rows:
        for i, val in enumerate(row):
            widths[i] = max(widths[i], len(str(val)))

    header_line = " | ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    separator_line = "-+-".join("-" * widths[i] for i in range(len(headers)))
    print(header_line)
    print(separator_line)
    for row in rows:
        print(" | ".join(str(val).ljust(widths[i]) for i, val in enumerate(row)))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Spike 01: Win32 Hardware Cursor Canvas & Latency Benchmark"
    )
    parser.add_argument(
        "--demo", action="store_true", help="Run visual demonstration sequence"
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=100,
        help="Number of iterations for latency measurements (default: 100)",
    )
    parser.add_argument(
        "--json-out",
        type=str,
        default="",
        help="Optional path to output raw telemetry JSON",
    )
    args = parser.parse_args()

    sizes = [32, 64, 128, 256]

    print("=================================================================")
    print("Spike 01: Hardware Cursor Canvas & Latency Benchmark")
    print("=================================================================")

    baseline = query_system_baseline()
    print("\n--- System Baseline ---")
    for k, v in baseline.items():
        print(f"{k.ljust(22)}: {v}")

    print("\n--- Canvas Dimension & Corner Pixel Fidelity ---")
    fidelity_rows: List[List[str]] = []
    for s in sizes:
        h = create_procedural_cursor(s, s, tint_argb=0xFFFFFFFF)
        w, h_dim, bpp = inspect_cursor_dimensions(h)
        user32.DestroyIcon(c_void_p(h))

        is_intact, val_00, val_corner = verify_corner_pixel_fidelity(s)
        fidelity_rows.append(
            [
                f"{s}x{s}",
                f"{w}x{h_dim}",
                f"{bpp} bpp",
                "Retained (No Downscale)" if is_intact else "Downscaled / Cropped",
                f"(0,0)={val_00}, (corner)={val_corner}",
            ]
        )
    print_table(
        ["Canvas Request", "Allocated HCURSOR", "Format", "OS Canvas Fidelity", "Corner Sample"],
        fidelity_rows,
    )

    print(f"\n--- In-Memory Handle Creation Latency ({args.iterations} Iterations) ---")
    creation_results = benchmark_creation_latency(sizes, iterations=args.iterations)
    creation_rows = []
    for s in sizes:
        st = creation_results[s]
        creation_rows.append(
            [
                f"{s}x{s}",
                f"{st['mean']:.2f}",
                f"{st['p50']:.2f}",
                f"{st['p95']:.2f}",
                f"{st['min']:.2f}",
                f"{st['max']:.2f}",
            ]
        )
    print_table(
        ["Size", "Mean (us)", "p50 (us)", "p95 (us)", "Min (us)", "Max (us)"],
        creation_rows,
    )

    print(f"\n--- Cached Handle Swap Latency ({args.iterations} Iterations) ---")
    swap_results = benchmark_swap_latency(sizes, iterations=args.iterations)
    swap_rows = []
    for s in sizes:
        st = swap_results[s]
        swap_rows.append(
            [
                f"{s}x{s}",
                f"{st['mean']:.2f}",
                f"{st['p50']:.2f}",
                f"{st['p95']:.2f}",
                f"{st['min']:.2f}",
                f"{st['max']:.2f}",
            ]
        )
    print_table(
        ["Size", "Mean (us)", "p50 (us)", "p95 (us)", "Min (us)", "Max (us)"],
        swap_rows,
    )

    print("\n--- 15-Frame Progress Sequence Transition Latency (64x64) ---")
    samples, anim_stats = benchmark_progress_animation_latency(size=64, frames=15)
    anim_rows = [
        [
            "15 Frames (0%..100%)",
            f"{anim_stats['mean']:.2f}",
            f"{anim_stats['p50']:.2f}",
            f"{anim_stats['p95']:.2f}",
            f"{anim_stats['min']:.2f}",
            f"{anim_stats['max']:.2f}",
        ]
    ]
    print_table(
        ["Sequence", "Mean (us)", "p50 (us)", "p95 (us)", "Min (us)", "Max (us)"],
        anim_rows,
    )

    if args.demo:
        run_visual_demonstration()

    if args.json_out:
        telemetry = {
            "baseline": baseline,
            "creation_latency_us": creation_results,
            "swap_latency_us": swap_results,
            "animation_latency_us": anim_stats,
        }
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(telemetry, f, indent=2)
        print(f"\nTelemetry saved to {args.json_out}")

    print("\n[SUCCESS] Benchmark suite completed. Windows cursor restored.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
