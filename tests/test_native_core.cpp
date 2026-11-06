// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Amir Farhadi

#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>

#include "bitmap_compositor.hpp"
#include "companion_overlay.hpp"
#include "hardware_driver.hpp"
#include "ipc_server.hpp"
#include "protocol.hpp"
#include "raii_handles.hpp"
#include "state_compositor.hpp"

#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <iostream>
#include <string>
#include <thread>
#include <vector>

#define TEST_ASSERT(condition) \
    do { \
        if (!(condition)) { \
            std::cerr << "Assertion failed: " #condition << " (" << __FILE__ << ":" << __LINE__ << ")\n"; \
            return false; \
        } \
    } while (0)

namespace {

using namespace dynamic_cursor;

bool test_binary_and_json_serialization() {
    std::cout << "[Test 1] Binary and JSON protocol serialization/deserialization\n";

    // Subtest A: Frame length encoding and decoding
    uint8_t header_buf[4]{};
    constexpr uint32_t kExpectedLength = 4096;
    encode_frame_length(kExpectedLength, header_buf);
    const uint32_t decoded_length = decode_frame_length(header_buf);
    TEST_ASSERT(decoded_length == kExpectedLength);

    // Subtest B: 32-byte binary packet serialization
    BinaryPacket packet{};
    packet.magic = kBinaryPacketMagic;
    packet.op = static_cast<uint8_t>(Operation::Upsert);
    packet.shape = static_cast<uint8_t>(BaseShape::ReadingMinimal);
    packet.badge = static_cast<uint16_t>(BadgeId::MicOff);
    packet.priority = 50;
    packet.tint_argb = 0xFFFF8000;
    packet.progress = 0.65f;
    packet.ttl_ms = 4500;
    packet.layer_hash = 9876;

    LayerPayload binary_payload{};
    uint8_t op_code = 0;
    const bool bin_parse_ok = parse_binary_frame(
        reinterpret_cast<const uint8_t*>(&packet),
        sizeof(packet),
        binary_payload,
        op_code
    );
    TEST_ASSERT(bin_parse_ok);
    TEST_ASSERT(op_code == static_cast<uint8_t>(Operation::Upsert));
    TEST_ASSERT(binary_payload.has_shape && binary_payload.shape == BaseShape::ReadingMinimal);
    TEST_ASSERT(binary_payload.has_badge && binary_payload.badge == BadgeId::MicOff);
    TEST_ASSERT(binary_payload.priority == 50);
    TEST_ASSERT(binary_payload.has_tint && binary_payload.tint_argb == 0xFFFF8000);
    TEST_ASSERT(binary_payload.has_progress && std::abs(binary_payload.progress - 0.65f) < 0.001f);
    TEST_ASSERT(binary_payload.ttl_ms == 4500);
    TEST_ASSERT(binary_payload.layer_id == "bin_9876");

    // Invalid binary packet magic check
    packet.magic = 0x11223344;
    TEST_ASSERT(!parse_binary_frame(reinterpret_cast<const uint8_t*>(&packet), sizeof(packet), binary_payload, op_code));

    // Subtest C: Length-prefixed JSON frame serialization
    const std::string json_data =
        R"({"op":"upsert","layer_id":"voice_layer","priority":75,"base_shape":"precision_cross",)"
        R"("badge_id":"vim_normal","tint_argb":"0xFF00FF00","progress":0.42,"ttl_ms":3000,)"
        R"("toast":{"text":"Recording Active","duration_ms":1200}})";

    LayerPayload json_payload{};
    const bool json_parse_ok = parse_json_frame(json_data, json_payload, op_code);
    TEST_ASSERT(json_parse_ok);
    TEST_ASSERT(op_code == static_cast<uint8_t>(Operation::Upsert));
    TEST_ASSERT(json_payload.layer_id == "voice_layer");
    TEST_ASSERT(json_payload.priority == 75);
    TEST_ASSERT(json_payload.has_shape && json_payload.shape == BaseShape::PrecisionCross);
    TEST_ASSERT(json_payload.has_badge && json_payload.badge == BadgeId::VimNormal);
    TEST_ASSERT(json_payload.has_tint && json_payload.tint_argb == 0xFF00FF00);
    TEST_ASSERT(json_payload.has_progress && std::abs(json_payload.progress - 0.42f) < 0.001f);
    TEST_ASSERT(json_payload.ttl_ms == 3000);
    TEST_ASSERT(json_payload.has_toast && json_payload.toast.text == "Recording Active" && json_payload.toast.duration_ms == 1200);

    // Malformed JSON check
    LayerPayload invalid_payload{};
    TEST_ASSERT(!parse_json_frame("{\"invalid_json", invalid_payload, op_code));

    return true;
}

bool test_multi_channel_priority_composition() {
    std::cout << "[Test 2] Multi-channel priority state composition\n";

    CursorStateCompositor compositor;
    TEST_ASSERT(compositor.active_layers_count() == 0);

    // Initial state check
    const CompositeState initial_state = compositor.evaluate_composite();
    TEST_ASSERT(initial_state.base_shape == BaseShape::DefaultArrow);
    TEST_ASSERT(initial_state.tint_argb == 0);
    TEST_ASSERT(initial_state.badge == BadgeId::None);
    TEST_ASSERT(!initial_state.has_progress());
    TEST_ASSERT(!initial_state.has_toast);

    // Layer 1 from Session 10: sets shape and tint
    LayerPayload layer_one{};
    layer_one.layer_id = "base_visuals";
    layer_one.priority = 10;
    layer_one.has_shape = true;
    layer_one.shape = BaseShape::ReadingMinimal;
    layer_one.has_tint = true;
    layer_one.tint_argb = 0xFFFF0000;
    compositor.upsert_layer(10, layer_one);

    CompositeState state = compositor.evaluate_composite();
    TEST_ASSERT(state.base_shape == BaseShape::ReadingMinimal);
    TEST_ASSERT(state.tint_argb == 0xFFFF0000);
    TEST_ASSERT(state.badge == BadgeId::None);

    // Layer 2 from Session 20: higher priority shape and new badge channel
    LayerPayload layer_two{};
    layer_two.layer_id = "cross_badge";
    layer_two.priority = 20;
    layer_two.has_shape = true;
    layer_two.shape = BaseShape::PrecisionCross;
    layer_two.has_badge = true;
    layer_two.badge = BadgeId::MicOff;
    compositor.upsert_layer(20, layer_two);

    state = compositor.evaluate_composite();
    TEST_ASSERT(state.base_shape == BaseShape::PrecisionCross);
    TEST_ASSERT(state.tint_argb == 0xFFFF0000); // Retained from layer 1 (orthogonal channel)
    TEST_ASSERT(state.badge == BadgeId::MicOff);

    // Layer 3 from Session 30: higher priority tint override
    LayerPayload layer_three{};
    layer_three.layer_id = "tint_override";
    layer_three.priority = 30;
    layer_three.has_tint = true;
    layer_three.tint_argb = 0xFF00FF00;
    compositor.upsert_layer(30, layer_three);

    state = compositor.evaluate_composite();
    TEST_ASSERT(state.tint_argb == 0xFF00FF00); // Overridden by layer 3
    TEST_ASSERT(state.base_shape == BaseShape::PrecisionCross);
    TEST_ASSERT(state.badge == BadgeId::MicOff);

    // Removal of Layer 3: tint falls back to layer 1
    compositor.remove_layer(30, "tint_override");
    state = compositor.evaluate_composite();
    TEST_ASSERT(state.tint_argb == 0xFFFF0000); // Restored to layer 1 tint
    TEST_ASSERT(state.base_shape == BaseShape::PrecisionCross);

    // TTL expiration validation
    LayerPayload ephemeral{};
    ephemeral.layer_id = "temp_alert";
    ephemeral.priority = 100;
    ephemeral.ttl_ms = 400;
    ephemeral.created_tick_ms = 1000;
    ephemeral.has_badge = true;
    ephemeral.badge = BadgeId::TvIcon;
    compositor.upsert_layer(40, ephemeral);

    state = compositor.purge_expired_layers(1200);
    TEST_ASSERT(state.badge == BadgeId::TvIcon); // Alive at t = 1200

    state = compositor.purge_expired_layers(1500);
    TEST_ASSERT(state.badge == BadgeId::MicOff); // Expired at t = 1500, restored to layer 2

    return true;
}

bool test_lru_cache_and_eviction() {
    std::cout << "[Test 3] Hardware driver LRU handle cache hit/miss and eviction\n";

    constexpr size_t kCapacity = 2;
    HardwareCursorDriver driver(32, kCapacity);
    TEST_ASSERT(driver.capacity() == kCapacity);
    TEST_ASSERT(driver.cached_count() == 0);

    CompositeState s1{};
    s1.base_shape = BaseShape::DefaultArrow;
    s1.tint_argb = 0xFF111111;

    CompositeState s2{};
    s2.base_shape = BaseShape::PrecisionCross;
    s2.tint_argb = 0xFF222222;

    CompositeState s3{};
    s3.base_shape = BaseShape::ReadingMinimal;
    s3.tint_argb = 0xFF333333;

    // Insert s1
    TEST_ASSERT(driver.apply_state(s1, true));
    TEST_ASSERT(driver.cached_count() == 1);
    TEST_ASSERT(driver.contains_hash(s1.compute_hash()));

    // Insert s2
    TEST_ASSERT(driver.apply_state(s2, true));
    TEST_ASSERT(driver.cached_count() == 2);
    TEST_ASSERT(driver.contains_hash(s2.compute_hash()));
    TEST_ASSERT(driver.contains_hash(s1.compute_hash()));

    // Re-apply s1 (cache hit, moves s1 to MRU)
    TEST_ASSERT(driver.apply_state(s1, true));
    TEST_ASSERT(driver.cached_count() == 2);

    // Insert s3 (capacity exceeded, s2 is LRU and should be evicted)
    TEST_ASSERT(driver.apply_state(s3, true));
    TEST_ASSERT(driver.cached_count() == 2);
    TEST_ASSERT(driver.contains_hash(s3.compute_hash()));
    TEST_ASSERT(driver.contains_hash(s1.compute_hash()));
    TEST_ASSERT(!driver.contains_hash(s2.compute_hash()));

    // Clear cache
    driver.clear_cache();
    TEST_ASSERT(driver.cached_count() == 0);

    return true;
}

bool test_handle_safety_and_duplication() {
    std::cout << "[Test 4] duplicate_cursor_for_system handle safety\n";

    TEST_ASSERT(duplicate_cursor_for_system(nullptr) == nullptr);

    CompositeState state{};
    state.base_shape = BaseShape::DefaultArrow;
    state.tint_argb = 0xFF00AAFF;

    UniqueCursor master = BitmapCompositor::compose(state, 32);
    TEST_ASSERT(master.get() != nullptr);

    HCURSOR duplicate_handle = duplicate_cursor_for_system(master.get());
    TEST_ASSERT(duplicate_handle != nullptr);
    TEST_ASSERT(duplicate_handle != master.get());

    // Destroy duplicate handle simulating user32!SetSystemCursor destruction
    DestroyIcon(duplicate_handle);

    // Master cursor must remain intact and valid
    ICONINFO icon_info{};
    const BOOL query_ok = GetIconInfo(master.get(), &icon_info);
    TEST_ASSERT(query_ok != FALSE);

    if (icon_info.hbmMask != nullptr) {
        DeleteObject(icon_info.hbmMask);
    }
    if (icon_info.hbmColor != nullptr) {
        DeleteObject(icon_info.hbmColor);
    }

    return true;
}

bool test_overlay_creation_and_styles() {
    std::cout << "[Test 5] Companion overlay window creation and extended styles\n";

    CompanionOverlay overlay;
    const HWND hwnd = overlay.hwnd();
    TEST_ASSERT(hwnd != nullptr);
    TEST_ASSERT(IsWindow(hwnd));

    const LONG ex_style = GetWindowLongW(hwnd, GWL_EXSTYLE);
    TEST_ASSERT((ex_style & WS_EX_TRANSPARENT) != 0);
    TEST_ASSERT((ex_style & WS_EX_NOACTIVATE) != 0);
    TEST_ASSERT((ex_style & WS_EX_LAYERED) != 0);
    TEST_ASSERT((ex_style & WS_EX_TOPMOST) != 0);

    TEST_ASSERT(!overlay.is_following());
    overlay.start_following();
    TEST_ASSERT(overlay.is_following());
    overlay.stop_following();
    TEST_ASSERT(!overlay.is_following());

    overlay.stop();
    TEST_ASSERT(overlay.hwnd() == nullptr);

    return true;
}

bool test_named_pipe_and_lease_rollback() {
    std::cout << "[Test 6] Named pipe connection and lease auto-rollback on disconnect\n";

    const std::wstring test_pipe = LR"(\\.\pipe\dynamic_cursor_test_lease)";
    CursorStateCompositor compositor;
    std::atomic<int> callback_invocations{0};

    auto on_state_changed = [&](const CompositeState&) {
        ++callback_invocations;
    };

    IpcServer server(test_pipe, on_state_changed, &compositor, 2);
    TEST_ASSERT(server.start());
    TEST_ASSERT(server.is_running());

    // Connect Win32 client handle to the named pipe
    HANDLE hclient = CreateFileW(
        test_pipe.c_str(),
        GENERIC_READ | GENERIC_WRITE,
        0,
        nullptr,
        OPEN_EXISTING,
        0,
        nullptr
    );
    TEST_ASSERT(hclient != INVALID_HANDLE_VALUE);

    // Wait briefly for server connection detection
    for (int i = 0; i < 40 && server.active_clients_count() == 0; ++i) {
        std::this_thread::sleep_for(std::chrono::milliseconds(25));
    }
    TEST_ASSERT(server.active_clients_count() == 1);

    // Write length-prefixed JSON layer payload
    const std::string payload = R"({"op":"upsert","layer":"lease_test","priority":50,"base_shape":"precision_cross"})";
    const uint32_t payload_len = static_cast<uint32_t>(payload.size());
    uint8_t header[4]{};
    encode_frame_length(payload_len, header);

    DWORD written = 0;
    const BOOL write_hdr_ok = WriteFile(hclient, header, sizeof(header), &written, nullptr);
    TEST_ASSERT(write_hdr_ok && written == sizeof(header));

    const BOOL write_body_ok = WriteFile(hclient, payload.data(), payload_len, &written, nullptr);
    TEST_ASSERT(write_body_ok && written == payload_len);
    FlushFileBuffers(hclient);

    // Wait for layer dispatch
    bool layer_active = false;
    for (int i = 0; i < 50; ++i) {
        if (compositor.active_layers_count() == 1 &&
            compositor.evaluate_composite().base_shape == BaseShape::PrecisionCross) {
            layer_active = true;
            break;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(25));
    }
    TEST_ASSERT(layer_active);

    // Close client handle simulating client disconnection or process crash
    CloseHandle(hclient);

    // Verify lease auto-rollback clears client layers
    bool rolled_back = false;
    for (int i = 0; i < 60; ++i) {
        if (compositor.active_layers_count() == 0 &&
            compositor.evaluate_composite().base_shape == BaseShape::DefaultArrow) {
            rolled_back = true;
            break;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(25));
    }
    TEST_ASSERT(rolled_back);

    server.stop();
    TEST_ASSERT(!server.is_running());

    return true;
}

} // namespace

int main() {
    std::cout << "Dynamic Cursor Native Core Test Runner\n";
    std::cout << "======================================\n";

    if (!test_binary_and_json_serialization()) {
        std::cerr << "FAILED: Binary and JSON serialization test\n";
        return 1;
    }

    if (!test_multi_channel_priority_composition()) {
        std::cerr << "FAILED: Multi-channel priority composition test\n";
        return 1;
    }

    if (!test_lru_cache_and_eviction()) {
        std::cerr << "FAILED: LRU cache test\n";
        return 1;
    }

    if (!test_handle_safety_and_duplication()) {
        std::cerr << "FAILED: Handle safety test\n";
        return 1;
    }

    if (!test_overlay_creation_and_styles()) {
        std::cerr << "FAILED: Overlay creation test\n";
        return 1;
    }

    if (!test_named_pipe_and_lease_rollback()) {
        std::cerr << "FAILED: Named pipe lease rollback test\n";
        return 1;
    }

    std::cout << "======================================\n";
    std::cout << "All native core unit tests passed successfully.\n";
    return 0;
}
