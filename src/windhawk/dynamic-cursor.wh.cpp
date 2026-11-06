// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Amir Farhadi

// ==WindhawkMod==
// @id              dynamic-cursor
// @name            Dynamic Cursor
// @description     Hardware cursor compositor and state actuator over Named Pipe
// @version         0.1.0
// @author          Amir Farhadi
// @compilerOptions -luser32 -lgdi32
// ==/WindhawkMod==

// ==WindhawkModSettings==
/*
- PipeName: "\\\\.\\pipe\\dynamic_cursor"
  $name: Named Pipe Path
  $description: Path of the IPC named pipe for layer streaming
- DefaultTint: 0
  $name: Default Cursor Tint (ARGB)
  $description: Default 32-bit ARGB hex color integer for cursor tinting
*/
// ==/WindhawkModSettings==

#ifndef WH_MOD
#define WH_MOD
#endif

#include <windhawk_api.h>

#include "bitmap_compositor.hpp"
#include "hardware_driver.hpp"
#include "protocol.hpp"
#include "raii_handles.hpp"
#include "state_compositor.hpp"

#include <atomic>
#include <cstdint>
#include <string>

namespace {

struct ModSettings {
    std::wstring pipe_name{dynamic_cursor::kDefaultPipeName};
    uint32_t default_tint{0};
} g_settings;

dynamic_cursor::UniqueCursor g_custom_cursor;
std::atomic<bool> g_hook_active{true};

using SetCursor_t = decltype(&SetCursor);
SetCursor_t g_original_set_cursor = nullptr;

/// Detour procedure intercepting application SetCursor calls.
HCURSOR WINAPI SetCursor_Hook(HCURSOR hcursor) {
    if (g_hook_active.load(std::memory_order_relaxed) && g_custom_cursor) {
        return g_original_set_cursor(g_custom_cursor.get());
    }
    return g_original_set_cursor(hcursor);
}

/// Loads mod configuration values from the Windhawk storage environment.
void load_settings() {
    PCWSTR pipe_setting = Wh_GetStringSetting(L"PipeName");
    if (pipe_setting != nullptr && pipe_setting[0] != L'\0') {
        g_settings.pipe_name = pipe_setting;
    } else {
        g_settings.pipe_name = dynamic_cursor::kDefaultPipeName;
    }
    Wh_FreeStringSetting(pipe_setting);

    g_settings.default_tint = static_cast<uint32_t>(Wh_GetIntSetting(L"DefaultTint"));

    if (g_settings.default_tint != 0) {
        dynamic_cursor::CompositeState state;
        state.tint_argb = g_settings.default_tint;
        state.base_shape = dynamic_cursor::BaseShape::DefaultArrow;
        g_custom_cursor = dynamic_cursor::BitmapCompositor::compose(state);
    } else {
        g_custom_cursor.reset();
    }
}

} // namespace

/// Windhawk mod initialization entry point.
BOOL Wh_ModInit() {
    Wh_Log(L"Dynamic Cursor mod initializing");
    load_settings();

    HMODULE user32_module = GetModuleHandleW(L"user32.dll");
    if (user32_module == nullptr) {
        user32_module = LoadLibraryW(L"user32.dll");
    }

    if (user32_module != nullptr) {
        auto* target_func = reinterpret_cast<void*>(GetProcAddress(user32_module, "SetCursor"));
        if (target_func != nullptr) {
            Wh_SetFunctionHook(
                target_func,
                reinterpret_cast<void*>(SetCursor_Hook),
                reinterpret_cast<void**>(&g_original_set_cursor)
            );
        }
    }

    return TRUE;
}

/// Windhawk mod termination cleanup entry point.
void Wh_ModUninit() {
    Wh_Log(L"Dynamic Cursor mod uninitializing");
    g_hook_active.store(false, std::memory_order_relaxed);
    g_custom_cursor.reset();
}

/// Windhawk settings reload entry point.
void Wh_ModSettingsChanged() {
    Wh_Log(L"Dynamic Cursor mod settings changed");
    load_settings();
}
