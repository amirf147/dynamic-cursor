// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Amir Farhadi

#pragma once

#ifndef DYNAMIC_CURSOR_STATE_COMPOSITOR_HPP
#define DYNAMIC_CURSOR_STATE_COMPOSITOR_HPP

#include "protocol.hpp"

#include <chrono>
#include <cstdint>
#include <cstring>
#include <map>
#include <mutex>
#include <optional>
#include <string>
#include <utility>
#include <vector>

namespace dynamic_cursor {

/// Returns the current monotonic clock timestamp in milliseconds.
[[nodiscard]] inline uint64_t current_monotonic_tick_ms() noexcept {
    const auto now = std::chrono::steady_clock::now().time_since_epoch();
    return static_cast<uint64_t>(std::chrono::duration_cast<std::chrono::milliseconds>(now).count());
}

/// Arbitrated composite cursor state across all orthogonal channels.
struct CompositeState {
    BaseShape base_shape{BaseShape::DefaultArrow};
    uint32_t tint_argb{0};
    BadgeId badge{BadgeId::None};
    float progress{-1.0f};
    ProgressStyle progress_style{ProgressStyle::Minimal};
    ToastSpec toast;
    bool has_toast{false};

    [[nodiscard]] constexpr bool has_progress() const noexcept {
        return progress >= 0.0f;
    }

    bool operator==(const CompositeState&) const = default;

    /// Computes a 64-bit FNV-1a hash representing the visual composite state.
    [[nodiscard]] uint64_t compute_hash() const noexcept {
        constexpr uint64_t kFnvOffsetBasis = 14695981039346656037ULL;
        constexpr uint64_t kFnvPrime = 1099511628211ULL;

        uint64_t hash = kFnvOffsetBasis;
        auto update_bytes = [&hash](const void* data, size_t size) noexcept {
            const auto* bytes = static_cast<const uint8_t*>(data);
            for (size_t i = 0; i < size; ++i) {
                hash ^= static_cast<uint64_t>(bytes[i]);
                hash *= kFnvPrime;
            }
        };

        const auto shape_val = static_cast<uint8_t>(base_shape);
        update_bytes(&shape_val, sizeof(shape_val));

        update_bytes(&tint_argb, sizeof(tint_argb));

        const auto badge_val = static_cast<uint16_t>(badge);
        update_bytes(&badge_val, sizeof(badge_val));

        uint32_t prog_bits = 0;
        std::memcpy(&prog_bits, &progress, sizeof(prog_bits));
        update_bytes(&prog_bits, sizeof(prog_bits));

        const auto style_val = static_cast<uint8_t>(progress_style);
        update_bytes(&style_val, sizeof(style_val));

        const uint8_t toast_flag = has_toast ? 1 : 0;
        update_bytes(&toast_flag, sizeof(toast_flag));

        if (has_toast) {
            update_bytes(&toast.duration_ms, sizeof(toast.duration_ms));
            const uint8_t follow_flag = toast.follow_cursor ? 1 : 0;
            update_bytes(&follow_flag, sizeof(follow_flag));
            update_bytes(toast.text.data(), toast.text.size());
        }

        return hash;
    }
};

/// Multi-channel priority compositor managing active feeder layers and client leases.
class CursorStateCompositor {
public:
    CursorStateCompositor() = default;
    ~CursorStateCompositor() = default;

    CursorStateCompositor(const CursorStateCompositor&) = delete;
    CursorStateCompositor& operator=(const CursorStateCompositor&) = delete;
    CursorStateCompositor(CursorStateCompositor&&) = delete;
    CursorStateCompositor& operator=(CursorStateCompositor&&) = delete;

    /// Upserts a visual layer asserted by a client session and re-evaluates composite state.
    CompositeState upsert_layer(uint64_t client_session_id, LayerPayload payload) {
        std::lock_guard<std::mutex> lock(mutex_);
        payload.client_session_id = client_session_id;
        if (payload.created_tick_ms == 0) {
            payload.created_tick_ms = current_monotonic_tick_ms();
        }
        if (payload.layer_id.empty()) {
            payload.layer_id = "session_" + std::to_string(client_session_id);
        }
        active_layers_[std::make_pair(client_session_id, payload.layer_id)] = std::move(payload);
        return evaluate_composite_locked();
    }

    /// Removes a layer by identifier and re-evaluates composite state.
    /// If client_session_id is non-zero, only layers belonging to that session are removed.
    CompositeState remove_layer(uint64_t client_session_id, const std::string& layer_id) {
        std::lock_guard<std::mutex> lock(mutex_);
        if (client_session_id != 0) {
            active_layers_.erase(std::make_pair(client_session_id, layer_id));
        } else {
            for (auto it = active_layers_.begin(); it != active_layers_.end();) {
                if (it->first.second == layer_id) {
                    it = active_layers_.erase(it);
                } else {
                    ++it;
                }
            }
        }
        return evaluate_composite_locked();
    }

    /// Clears all layers associated with a disconnected client session (kernel lease rollback).
    CompositeState clear_client_layers(uint64_t client_session_id) {
        std::lock_guard<std::mutex> lock(mutex_);
        for (auto it = active_layers_.begin(); it != active_layers_.end();) {
            if (it->first.first == client_session_id) {
                it = active_layers_.erase(it);
            } else {
                ++it;
            }
        }
        return evaluate_composite_locked();
    }

    /// Purges layers exceeding their specified time-to-live duration.
    /// Uses now_tick_ms if supplied, or samples the monotonic clock if zero.
    CompositeState purge_expired_layers(uint64_t now_tick_ms = 0) {
        std::lock_guard<std::mutex> lock(mutex_);
        if (now_tick_ms == 0) {
            now_tick_ms = current_monotonic_tick_ms();
        }
        for (auto it = active_layers_.begin(); it != active_layers_.end();) {
            const auto& layer = it->second;
            if (layer.ttl_ms > 0 && now_tick_ms >= (layer.created_tick_ms + layer.ttl_ms)) {
                it = active_layers_.erase(it);
            } else {
                ++it;
            }
        }
        return evaluate_composite_locked();
    }

    /// Returns the earliest deadline timestamp in milliseconds across active layers with ttl_ms > 0.
    /// Returns 0 if no active layer has an expiration deadline.
    [[nodiscard]] uint64_t earliest_ttl_deadline_ms() const {
        std::lock_guard<std::mutex> lock(mutex_);
        uint64_t earliest = 0;
        for (const auto& [key, layer] : active_layers_) {
            if (layer.ttl_ms > 0) {
                const uint64_t deadline = layer.created_tick_ms + layer.ttl_ms;
                if (earliest == 0 || deadline < earliest) {
                    earliest = deadline;
                }
            }
        }
        return earliest;
    }

    /// Evaluates active layers across orthogonal channels and returns the composite state.
    [[nodiscard]] CompositeState evaluate_composite() const {
        std::lock_guard<std::mutex> lock(mutex_);
        return evaluate_composite_locked();
    }

    /// Returns the number of currently active layers.
    [[nodiscard]] size_t active_layers_count() const {
        std::lock_guard<std::mutex> lock(mutex_);
        return active_layers_.size();
    }

    /// Finds an active layer by session id and layer identifier.
    [[nodiscard]] std::optional<LayerPayload> get_layer(uint64_t client_session_id, const std::string& layer_id) const {
        std::lock_guard<std::mutex> lock(mutex_);
        auto it = active_layers_.find(std::make_pair(client_session_id, layer_id));
        if (it != active_layers_.end()) {
            return it->second;
        }
        return std::nullopt;
    }

    /// Finds the first active layer matching the layer identifier.
    [[nodiscard]] std::optional<LayerPayload> get_layer(const std::string& layer_id) const {
        std::lock_guard<std::mutex> lock(mutex_);
        for (const auto& [key, layer] : active_layers_) {
            if (key.second == layer_id) {
                return layer;
            }
        }
        return std::nullopt;
    }

    /// Clears all active layers across all sessions.
    CompositeState clear_all() {
        std::lock_guard<std::mutex> lock(mutex_);
        active_layers_.clear();
        return evaluate_composite_locked();
    }

private:
    /// Merges highest priority non-conflicting channels across all active layers.
    /// Caller must hold mutex_.
    [[nodiscard]] CompositeState evaluate_composite_locked() const noexcept {
        CompositeState state;

        int32_t shape_priority = INT32_MIN;
        uint64_t shape_tick = 0;
        bool shape_set = false;

        int32_t tint_priority = INT32_MIN;
        uint64_t tint_tick = 0;
        bool tint_set = false;

        int32_t badge_priority = INT32_MIN;
        uint64_t badge_tick = 0;
        bool badge_set = false;

        int32_t progress_priority = INT32_MIN;
        uint64_t progress_tick = 0;
        bool progress_set = false;

        int32_t toast_priority = INT32_MIN;
        uint64_t toast_tick = 0;
        bool toast_set = false;

        for (const auto& [key, layer] : active_layers_) {
            if (layer.has_shape) {
                if (!shape_set || layer.priority > shape_priority ||
                    (layer.priority == shape_priority && layer.created_tick_ms >= shape_tick)) {
                    state.base_shape = layer.shape;
                    shape_priority = layer.priority;
                    shape_tick = layer.created_tick_ms;
                    shape_set = true;
                }
            }

            if (layer.has_tint) {
                if (!tint_set || layer.priority > tint_priority ||
                    (layer.priority == tint_priority && layer.created_tick_ms >= tint_tick)) {
                    state.tint_argb = layer.tint_argb;
                    tint_priority = layer.priority;
                    tint_tick = layer.created_tick_ms;
                    tint_set = true;
                }
            }

            if (layer.has_badge) {
                if (!badge_set || layer.priority > badge_priority ||
                    (layer.priority == badge_priority && layer.created_tick_ms >= badge_tick)) {
                    state.badge = layer.badge;
                    badge_priority = layer.priority;
                    badge_tick = layer.created_tick_ms;
                    badge_set = true;
                }
            }

            if (layer.has_progress) {
                if (!progress_set || layer.priority > progress_priority ||
                    (layer.priority == progress_priority && layer.created_tick_ms >= progress_tick)) {
                    state.progress = layer.progress;
                    state.progress_style = layer.has_progress_style ? layer.progress_style : ProgressStyle::Minimal;
                    progress_priority = layer.priority;
                    progress_tick = layer.created_tick_ms;
                    progress_set = true;
                }
            }

            if (layer.has_toast) {
                if (!toast_set || layer.priority > toast_priority ||
                    (layer.priority == toast_priority && layer.created_tick_ms >= toast_tick)) {
                    state.toast = layer.toast;
                    state.has_toast = true;
                    toast_priority = layer.priority;
                    toast_tick = layer.created_tick_ms;
                    toast_set = true;
                }
            }
        }

        return state;
    }

    mutable std::mutex mutex_;
    std::map<std::pair<uint64_t, std::string>, LayerPayload> active_layers_;
};

} // namespace dynamic_cursor

#endif // DYNAMIC_CURSOR_STATE_COMPOSITOR_HPP
