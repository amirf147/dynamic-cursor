// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Amir Farhadi

#pragma once

#ifndef DYNAMIC_CURSOR_BITMAP_COMPOSITOR_HPP
#define DYNAMIC_CURSOR_BITMAP_COMPOSITOR_HPP

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>

#include "protocol.hpp"
#include "raii_handles.hpp"
#include "state_compositor.hpp"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <utility>
#include <vector>

namespace dynamic_cursor {

/// Calculates 32-bit pre-multiplied ARGB channel values.
/// GDI and hardware cursor scanout require channels pre-multiplied by alpha.
[[nodiscard]] inline constexpr uint32_t premultiply_argb(uint8_t a, uint8_t r, uint8_t g, uint8_t b) noexcept {
    const uint32_t alpha = static_cast<uint32_t>(a);
    const uint32_t pr = (static_cast<uint32_t>(r) * alpha) / 255U;
    const uint32_t pg = (static_cast<uint32_t>(g) * alpha) / 255U;
    const uint32_t pb = (static_cast<uint32_t>(b) * alpha) / 255U;
    return (alpha << 24) | (pr << 16) | (pg << 8) | pb;
}

/// Overload calculating pre-multiplied ARGB from an existing ARGB integer.
[[nodiscard]] inline constexpr uint32_t premultiply_argb(uint32_t argb) noexcept {
    const uint8_t a = static_cast<uint8_t>((argb >> 24) & 0xFF);
    const uint8_t r = static_cast<uint8_t>((argb >> 16) & 0xFF);
    const uint8_t g = static_cast<uint8_t>((argb >> 8) & 0xFF);
    const uint8_t b = static_cast<uint8_t>(argb & 0xFF);
    return premultiply_argb(a, r, g, b);
}

namespace detail {

inline constexpr const char* kArrowMask[19] = {
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
    "       XX       "
};

inline void set_pixel(uint32_t* buffer, int width, int height, int x, int y, uint32_t color) noexcept {
    if (x >= 0 && x < width && y >= 0 && y < height) {
        buffer[y * width + x] = color;
    }
}

} // namespace detail

/// Renders classic crisp Windows arrow with dark outline and tinted fill directly into buffer.
inline void draw_arrow(uint32_t* buffer, int width, int height, uint32_t tint_argb) noexcept {
    if (buffer == nullptr || width <= 0 || height <= 0) {
        return;
    }

    uint32_t fill_color = 0xFFFFFFFF;
    if (tint_argb != 0) {
        if ((tint_argb & 0xFF000000) == 0) {
            tint_argb |= 0xFF000000;
        }
        fill_color = premultiply_argb(tint_argb);
    }
    constexpr uint32_t border_color = 0xFF000000;

    const int scale = (width >= 128) ? 2 : 1;
    for (int y = 0; y < 19; ++y) {
        for (int x = 0; x < 16; ++x) {
            const char c = detail::kArrowMask[y][x];
            if (c == ' ') {
                continue;
            }
            const uint32_t color = (c == 'X') ? border_color : fill_color;
            for (int dy = 0; dy < scale; ++dy) {
                const int py = y * scale + dy;
                if (py >= height) {
                    break;
                }
                for (int dx = 0; dx < scale; ++dx) {
                    const int px = x * scale + dx;
                    if (px >= width) {
                        break;
                    }
                    buffer[py * width + px] = color;
                }
            }
        }
    }
}

/// Renders crisp crosshair with center gap directly into buffer.
inline void draw_precision_cross(uint32_t* buffer, int width, int height, uint32_t tint_argb) noexcept {
    if (buffer == nullptr || width <= 0 || height <= 0) {
        return;
    }

    uint32_t fill_color = 0xFFFFFFFF;
    if (tint_argb != 0) {
        if ((tint_argb & 0xFF000000) == 0) {
            tint_argb |= 0xFF000000;
        }
        fill_color = premultiply_argb(tint_argb);
    }
    constexpr uint32_t border_color = 0xFF000000;

    const int scale = (width >= 128) ? 2 : 1;
    const int cx = 10 * scale;
    const int cy = 10 * scale;
    const int radius = 9 * scale;
    const int gap = 3 * scale;

    // Horizontal arm left and right
    for (int dx = gap; dx <= radius; ++dx) {
        for (int sign : {-1, 1}) {
            const int px = cx + sign * dx;
            detail::set_pixel(buffer, width, height, px, cy, fill_color);
            detail::set_pixel(buffer, width, height, px, cy - 1, border_color);
            detail::set_pixel(buffer, width, height, px, cy + 1, border_color);
        }
    }

    // Horizontal arm end caps and gap caps
    detail::set_pixel(buffer, width, height, cx - radius - 1, cy, border_color);
    detail::set_pixel(buffer, width, height, cx + radius + 1, cy, border_color);
    detail::set_pixel(buffer, width, height, cx - gap + 1, cy, border_color);
    detail::set_pixel(buffer, width, height, cx + gap - 1, cy, border_color);

    // Vertical arm top and bottom
    for (int dy = gap; dy <= radius; ++dy) {
        for (int sign : {-1, 1}) {
            const int py = cy + sign * dy;
            detail::set_pixel(buffer, width, height, cx, py, fill_color);
            detail::set_pixel(buffer, width, height, cx - 1, py, border_color);
            detail::set_pixel(buffer, width, height, cx + 1, py, border_color);
        }
    }

    // Vertical arm end caps and gap caps
    detail::set_pixel(buffer, width, height, cx, cy - radius - 1, border_color);
    detail::set_pixel(buffer, width, height, cx, cy + radius + 1, border_color);
    detail::set_pixel(buffer, width, height, cx, cy - gap + 1, border_color);
    detail::set_pixel(buffer, width, height, cx, cy + gap - 1, border_color);
}

/// Renders minimal slim dot or thin reading caret directly into buffer.
inline void draw_reading_minimal(uint32_t* buffer, int width, int height, uint32_t tint_argb) noexcept {
    if (buffer == nullptr || width <= 0 || height <= 0) {
        return;
    }

    uint32_t fill_color = 0xFFFFFFFF;
    if (tint_argb != 0) {
        if ((tint_argb & 0xFF000000) == 0) {
            tint_argb |= 0xFF000000;
        }
        fill_color = premultiply_argb(tint_argb);
    }
    constexpr uint32_t border_color = 0xFF000000;

    const int scale = (width >= 128) ? 2 : 1;
    const int cx = 8 * scale;
    const int bar_len = 3 * scale;

    // Top serif
    for (int dx = -bar_len; dx <= bar_len; ++dx) {
        detail::set_pixel(buffer, width, height, cx + dx, 1 * scale, border_color);
        detail::set_pixel(buffer, width, height, cx + dx, 2 * scale, fill_color);
    }
    detail::set_pixel(buffer, width, height, cx - bar_len - 1, 2 * scale, border_color);
    detail::set_pixel(buffer, width, height, cx + bar_len + 1, 2 * scale, border_color);

    // Stem
    for (int y = 3 * scale; y <= 16 * scale; ++y) {
        detail::set_pixel(buffer, width, height, cx - 1, y, border_color);
        detail::set_pixel(buffer, width, height, cx, y, fill_color);
        detail::set_pixel(buffer, width, height, cx + 1, y, border_color);
    }

    // Center indicator dot
    for (int dy = -1; dy <= 1; ++dy) {
        for (int dx = -1; dx <= 1; ++dx) {
            detail::set_pixel(buffer, width, height, cx + dx, 9 * scale + dy, fill_color);
        }
    }
    detail::set_pixel(buffer, width, height, cx - 2, 9 * scale, border_color);
    detail::set_pixel(buffer, width, height, cx + 2, 9 * scale, border_color);

    // Bottom serif
    for (int dx = -bar_len; dx <= bar_len; ++dx) {
        detail::set_pixel(buffer, width, height, cx + dx, 17 * scale, fill_color);
        detail::set_pixel(buffer, width, height, cx + dx, 18 * scale, border_color);
    }
    detail::set_pixel(buffer, width, height, cx - bar_len - 1, 17 * scale, border_color);
    detail::set_pixel(buffer, width, height, cx + bar_len + 1, 17 * scale, border_color);
}

/// Calculates tight proximity anchor (x0, y0) based on base pointer silhouette and display scale.
[[nodiscard]] inline constexpr std::pair<int, int> compute_badge_anchor(BaseShape shape, int scale = 1) noexcept {
    const int s = (scale > 0) ? scale : 1;
    switch (shape) {
        case BaseShape::PrecisionCross:
            return {14 * s, 14 * s};
        case BaseShape::ReadingMinimal:
            return {14 * s, 8 * s};
        case BaseShape::DefaultArrow:
        default:
            return {14 * s, 12 * s};
    }
}

/// Renders 16x16 icon at [x0, y0] to [x0 + 15, y0 + 15].
/// If x0 or y0 is negative, computes anchor coordinates dynamically relative to the default arrow.
inline void draw_badge(
    uint32_t* buffer,
    int width,
    int height,
    BadgeId badge,
    int x0 = -1,
    int y0 = -1
) noexcept {
    if (buffer == nullptr || badge == BadgeId::None) {
        return;
    }

    if (x0 < 0 || y0 < 0) {
        const int scale = (width >= 128) ? 2 : 1;
        const auto [def_x, def_y] = compute_badge_anchor(BaseShape::DefaultArrow, scale);
        x0 = def_x;
        y0 = def_y;
    }

    if (x0 < 0 || y0 < 0 || x0 >= width || y0 >= height) {
        return;
    }

    constexpr uint32_t kWhite = 0xFFFFFFFF;
    constexpr uint32_t kBlack = 0xFF000000;

    switch (badge) {
        case BadgeId::MicOff: {
            constexpr uint32_t kRedBg = 0xFFE53935;
            constexpr uint32_t kRedBorder = 0xFF4A0000;
            constexpr uint32_t kSlashShadow = 0xFF2B0000;

            for (int ry = 0; ry < 16; ++ry) {
                for (int rx = 0; rx < 16; ++rx) {
                    const float dx = static_cast<float>(rx) - 7.5f;
                    const float dy = static_cast<float>(ry) - 7.5f;
                    const float d2 = dx * dx + dy * dy;
                    if (d2 <= 56.25f) {
                        const uint32_t c = (d2 > 42.0f) ? kRedBorder : kRedBg;
                        detail::set_pixel(buffer, width, height, x0 + rx, y0 + ry, c);
                    }
                }
            }

            // Microphone silhouette
            for (int ry = 4; ry <= 7; ++ry) {
                detail::set_pixel(buffer, width, height, x0 + 7, y0 + ry, kWhite);
                detail::set_pixel(buffer, width, height, x0 + 8, y0 + ry, kWhite);
            }
            detail::set_pixel(buffer, width, height, x0 + 5, y0 + 7, kWhite);
            detail::set_pixel(buffer, width, height, x0 + 5, y0 + 8, kWhite);
            detail::set_pixel(buffer, width, height, x0 + 10, y0 + 7, kWhite);
            detail::set_pixel(buffer, width, height, x0 + 10, y0 + 8, kWhite);
            for (int rx = 6; rx <= 9; ++rx) {
                detail::set_pixel(buffer, width, height, x0 + rx, y0 + 9, kWhite);
            }
            detail::set_pixel(buffer, width, height, x0 + 7, y0 + 10, kWhite);
            detail::set_pixel(buffer, width, height, x0 + 8, y0 + 10, kWhite);
            for (int rx = 5; rx <= 10; ++rx) {
                detail::set_pixel(buffer, width, height, x0 + rx, y0 + 11, kWhite);
            }

            // Diagonal slash
            for (int i = 3; i <= 12; ++i) {
                detail::set_pixel(buffer, width, height, x0 + i - 1, y0 + i, kSlashShadow);
                detail::set_pixel(buffer, width, height, x0 + i, y0 + i, kWhite);
                detail::set_pixel(buffer, width, height, x0 + i + 1, y0 + i, kSlashShadow);
            }
            break;
        }

        case BadgeId::MicOn: {
            constexpr uint32_t kGreenBg = 0xFF00C853;
            constexpr uint32_t kGreenBorder = 0xFF003810;
            constexpr uint32_t kGreenShadow = 0xFF004D20;

            for (int ry = 0; ry < 16; ++ry) {
                for (int rx = 0; rx < 16; ++rx) {
                    const float dx = static_cast<float>(rx) - 7.5f;
                    const float dy = static_cast<float>(ry) - 7.5f;
                    const float d2 = dx * dx + dy * dy;
                    if (d2 <= 56.25f) {
                        const uint32_t c = (d2 > 42.0f) ? kGreenBorder : kGreenBg;
                        detail::set_pixel(buffer, width, height, x0 + rx, y0 + ry, c);
                    }
                }
            }

            // Microphone silhouette
            for (int ry = 3; ry <= 7; ++ry) {
                detail::set_pixel(buffer, width, height, x0 + 6, y0 + ry, kGreenShadow);
                detail::set_pixel(buffer, width, height, x0 + 7, y0 + ry, kWhite);
                detail::set_pixel(buffer, width, height, x0 + 8, y0 + ry, kWhite);
                detail::set_pixel(buffer, width, height, x0 + 9, y0 + ry, kGreenShadow);
            }
            detail::set_pixel(buffer, width, height, x0 + 5, y0 + 6, kWhite);
            detail::set_pixel(buffer, width, height, x0 + 5, y0 + 7, kWhite);
            detail::set_pixel(buffer, width, height, x0 + 5, y0 + 8, kWhite);
            detail::set_pixel(buffer, width, height, x0 + 10, y0 + 6, kWhite);
            detail::set_pixel(buffer, width, height, x0 + 10, y0 + 7, kWhite);
            detail::set_pixel(buffer, width, height, x0 + 10, y0 + 8, kWhite);
            for (int rx = 6; rx <= 9; ++rx) {
                detail::set_pixel(buffer, width, height, x0 + rx, y0 + 8, kWhite);
            }
            detail::set_pixel(buffer, width, height, x0 + 7, y0 + 9, kWhite);
            detail::set_pixel(buffer, width, height, x0 + 8, y0 + 9, kWhite);
            detail::set_pixel(buffer, width, height, x0 + 7, y0 + 10, kWhite);
            detail::set_pixel(buffer, width, height, x0 + 8, y0 + 10, kWhite);
            for (int rx = 5; rx <= 10; ++rx) {
                detail::set_pixel(buffer, width, height, x0 + rx, y0 + 11, kWhite);
            }
            break;
        }

        case BadgeId::TvIcon: {
            constexpr uint32_t kBezelBlue = 0xFF0D47A1;
            constexpr uint32_t kScreenBlue = 0xFF42A5F5;
            constexpr uint32_t kScreenGlow = 0xFF90CAF9;
            constexpr uint32_t kAntenna = 0xFFE2E8F0;

            // Antenna lines
            detail::set_pixel(buffer, width, height, x0 + 7, y0 + 4, kAntenna);
            detail::set_pixel(buffer, width, height, x0 + 6, y0 + 3, kAntenna);
            detail::set_pixel(buffer, width, height, x0 + 5, y0 + 2, kAntenna);
            detail::set_pixel(buffer, width, height, x0 + 4, y0 + 1, kAntenna);

            detail::set_pixel(buffer, width, height, x0 + 8, y0 + 4, kAntenna);
            detail::set_pixel(buffer, width, height, x0 + 9, y0 + 3, kAntenna);
            detail::set_pixel(buffer, width, height, x0 + 10, y0 + 2, kAntenna);
            detail::set_pixel(buffer, width, height, x0 + 11, y0 + 1, kAntenna);

            // Screen outer frame
            for (int ry = 5; ry <= 13; ++ry) {
                for (int rx = 2; rx <= 13; ++rx) {
                    if (ry == 5 || ry == 13 || rx == 2 || rx == 13) {
                        detail::set_pixel(buffer, width, height, x0 + rx, y0 + ry, kBlack);
                    } else {
                        detail::set_pixel(buffer, width, height, x0 + rx, y0 + ry, kBezelBlue);
                    }
                }
            }

            // Screen interior display
            for (int ry = 7; ry <= 11; ++ry) {
                for (int rx = 4; rx <= 10; ++rx) {
                    detail::set_pixel(buffer, width, height, x0 + rx, y0 + ry, kScreenBlue);
                }
            }
            detail::set_pixel(buffer, width, height, x0 + 4, y0 + 7, kScreenGlow);
            detail::set_pixel(buffer, width, height, x0 + 5, y0 + 7, kScreenGlow);
            detail::set_pixel(buffer, width, height, x0 + 4, y0 + 8, kScreenGlow);

            // Controls on right bezel
            detail::set_pixel(buffer, width, height, x0 + 11, y0 + 7, kWhite);
            detail::set_pixel(buffer, width, height, x0 + 11, y0 + 9, kWhite);

            // Stand
            detail::set_pixel(buffer, width, height, x0 + 7, y0 + 14, kBlack);
            detail::set_pixel(buffer, width, height, x0 + 8, y0 + 14, kBlack);
            for (int rx = 5; rx <= 10; ++rx) {
                detail::set_pixel(buffer, width, height, x0 + rx, y0 + 15, kBlack);
            }
            break;
        }

        case BadgeId::VimNormal: {
            constexpr uint32_t kSlateBg = 0xFF1E293B;

            // Slate background rectangle with clipped corners
            for (int ry = 1; ry <= 14; ++ry) {
                for (int rx = 1; rx <= 14; ++rx) {
                    const bool corner = (rx == 1 || rx == 14) && (ry == 1 || ry == 14);
                    if (corner) {
                        continue;
                    }
                    const bool edge = (rx == 1 || rx == 14 || ry == 1 || ry == 14);
                    const uint32_t c = edge ? kBlack : kSlateBg;
                    detail::set_pixel(buffer, width, height, x0 + rx, y0 + ry, c);
                }
            }

            // Crisp 'N' letter
            for (int ry = 4; ry <= 11; ++ry) {
                detail::set_pixel(buffer, width, height, x0 + 4, y0 + ry, kWhite);
                detail::set_pixel(buffer, width, height, x0 + 5, y0 + ry, kWhite);
                detail::set_pixel(buffer, width, height, x0 + 10, y0 + ry, kWhite);
                detail::set_pixel(buffer, width, height, x0 + 11, y0 + ry, kWhite);
            }

            // Diagonal bar
            detail::set_pixel(buffer, width, height, x0 + 5, y0 + 5, kWhite);
            detail::set_pixel(buffer, width, height, x0 + 6, y0 + 6, kWhite);
            detail::set_pixel(buffer, width, height, x0 + 7, y0 + 7, kWhite);
            detail::set_pixel(buffer, width, height, x0 + 8, y0 + 8, kWhite);
            detail::set_pixel(buffer, width, height, x0 + 9, y0 + 9, kWhite);
            detail::set_pixel(buffer, width, height, x0 + 10, y0 + 10, kWhite);
            break;
        }

        case BadgeId::None:
        default:
            break;
    }
}

/// Renders compact 56x6 horizontal progress track with dark background and vibrant fill.
/// If custom_y0 is negative, defaults to y0 = 32 * scale (or y0 = 24 * scale if badge is absent).
inline void draw_progress(
    uint32_t* buffer,
    int width,
    int height,
    float ratio,
    int custom_y0 = -1
) noexcept {
    if (buffer == nullptr || width <= 0 || height <= 0) {
        return;
    }

    constexpr int kBarHeight = 6;
    constexpr int kBarWidth = 56;
    const int scale = (width >= 128) ? 2 : 1;
    const int y0 = (custom_y0 >= 0) ? custom_y0 : (32 * scale);
    if (y0 < 0 || y0 + kBarHeight > height) {
        return;
    }
    const int x0 = (width >= kBarWidth) ? ((width - kBarWidth) / 2) : 0;

    const float clamped_ratio = (ratio < 0.0f) ? 0.0f : (ratio > 1.0f ? 1.0f : ratio);
    const int fill_w = static_cast<int>(std::round(54.0f * clamped_ratio));

    constexpr uint32_t kBorderColor = 0xFF000000;
    constexpr uint32_t kFillColor = 0xFF00E5FF;
    constexpr uint32_t kTrackColor = 0xFF202020;

    for (int dy = 0; dy < kBarHeight; ++dy) {
        const int py = y0 + dy;
        for (int dx = 0; dx < kBarWidth; ++dx) {
            const int px = x0 + dx;
            if (dy == 0 || dy == kBarHeight - 1 || dx == 0 || dx == kBarWidth - 1) {
                detail::set_pixel(buffer, width, height, px, py, kBorderColor);
            } else {
                const int inner_x = dx - 1;
                const uint32_t color = (inner_x < fill_w) ? kFillColor : kTrackColor;
                detail::set_pixel(buffer, width, height, px, py, color);
            }
        }
    }
}

/// Generates a 32-bit pre-multiplied ARGB DIBSection cursor handle for composite state layers.
class BitmapCompositor {
public:
    /// Composites active visual state channels into a hardware cursor handle.
    [[nodiscard]] static UniqueCursor compose(const CompositeState& state, int canvas_size = 64) {
        if (canvas_size <= 0) {
            canvas_size = 64;
        }

        BITMAPINFO bmi = {};
        bmi.bmiHeader.biSize = sizeof(BITMAPINFOHEADER);
        bmi.bmiHeader.biWidth = canvas_size;
        bmi.bmiHeader.biHeight = -canvas_size;
        bmi.bmiHeader.biPlanes = 1;
        bmi.bmiHeader.biBitCount = 32;
        bmi.bmiHeader.biCompression = BI_RGB;

        uint32_t* pixel_buffer = nullptr;
        HDC hdc_screen = GetDC(nullptr);
        HBITMAP hbmColor = CreateDIBSection(
            hdc_screen,
            &bmi,
            DIB_RGB_COLORS,
            reinterpret_cast<void**>(&pixel_buffer),
            nullptr,
            0
        );
        ReleaseDC(nullptr, hdc_screen);

        if (!hbmColor || !pixel_buffer) {
            if (hbmColor) {
                DeleteObject(hbmColor);
            }
            return UniqueCursor{};
        }

        const size_t total_pixels = static_cast<size_t>(canvas_size * canvas_size);
        std::fill_n(pixel_buffer, total_pixels, 0x00000000U);

        const int scale = (canvas_size >= 128) ? 2 : 1;

        switch (state.base_shape) {
            case BaseShape::PrecisionCross:
                draw_precision_cross(pixel_buffer, canvas_size, canvas_size, state.tint_argb);
                break;
            case BaseShape::ReadingMinimal:
                draw_reading_minimal(pixel_buffer, canvas_size, canvas_size, state.tint_argb);
                break;
            case BaseShape::DefaultArrow:
            default:
                draw_arrow(pixel_buffer, canvas_size, canvas_size, state.tint_argb);
                break;
        }

        if (state.badge != BadgeId::None) {
            const auto [bx, by] = compute_badge_anchor(state.base_shape, scale);
            draw_badge(pixel_buffer, canvas_size, canvas_size, state.badge, bx, by);
        }

        if (state.progress >= 0.0f) {
            const int prog_y = (state.badge != BadgeId::None) ? (32 * scale) : (24 * scale);
            draw_progress(pixel_buffer, canvas_size, canvas_size, state.progress, prog_y);
        }

        HBITMAP hbmMask = CreateBitmap(canvas_size, canvas_size, 1, 1, nullptr);
        if (!hbmMask) {
            const int mask_pitch = ((canvas_size + 31) / 32) * 4;
            std::vector<uint8_t> mask_bits(static_cast<size_t>(mask_pitch * canvas_size), 0);
            hbmMask = CreateBitmap(canvas_size, canvas_size, 1, 1, mask_bits.data());
        }

        if (!hbmMask) {
            DeleteObject(hbmColor);
            return UniqueCursor{};
        }

        ICONINFO icon_info = {};
        icon_info.fIcon = FALSE;
        icon_info.xHotspot = 0;
        icon_info.yHotspot = 0;
        icon_info.hbmMask = hbmMask;
        icon_info.hbmColor = hbmColor;

        HCURSOR hCursor = CreateIconIndirect(&icon_info);

        DeleteObject(hbmColor);
        DeleteObject(hbmMask);

        return UniqueCursor(hCursor);
    }
};

} // namespace dynamic_cursor

#endif // DYNAMIC_CURSOR_BITMAP_COMPOSITOR_HPP
