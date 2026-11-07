// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Amir Farhadi

#pragma once

#ifndef DYNAMIC_CURSOR_HARDWARE_DRIVER_HPP
#define DYNAMIC_CURSOR_HARDWARE_DRIVER_HPP

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>

#include "bitmap_compositor.hpp"
#include "protocol.hpp"
#include "raii_handles.hpp"
#include "state_compositor.hpp"

#include <cstdint>
#include <list>
#include <mutex>
#include <unordered_map>
#include <vector>

#ifndef OCR_NORMAL
#define OCR_NORMAL 32512
#endif

#ifndef OCR_IBEAM
#define OCR_IBEAM 32513
#endif

#ifndef OCR_HAND
#define OCR_HAND 32649
#endif

#ifndef SPI_SETCURSORS
#define SPI_SETCURSORS 0x0057
#endif

namespace dynamic_cursor {

/// Hash functor for arbitrated composite cursor state.
struct CompositeStateHash {
    [[nodiscard]] uint64_t operator()(const CompositeState& state) const noexcept {
        return state.compute_hash();
    }
    [[nodiscard]] static uint64_t compute(const CompositeState& state) noexcept {
        return state.compute_hash();
    }
};

/// Hardware cursor plane driver managing cached master handles and system cursor assignment.
class HardwareCursorDriver {
public:
    static constexpr size_t kDefaultCapacity = 64;
    static constexpr int kDefaultCanvasSize = 64;

    explicit HardwareCursorDriver(int canvas_size = kDefaultCanvasSize, size_t capacity = kDefaultCapacity) noexcept
        : canvas_size_(canvas_size), capacity_(capacity > 0 ? capacity : kDefaultCapacity) {}

    ~HardwareCursorDriver() = default;

    HardwareCursorDriver(const HardwareCursorDriver&) = delete;
    HardwareCursorDriver& operator=(const HardwareCursorDriver&) = delete;
    HardwareCursorDriver(HardwareCursorDriver&&) = delete;
    HardwareCursorDriver& operator=(HardwareCursorDriver&&) = delete;

    /// Restores the operating system default cursor scheme via SPI_SETCURSORS.
    static bool restore_defaults() noexcept {
        return SystemParametersInfoW(SPI_SETCURSORS, 0, nullptr, 0) != FALSE;
    }

    /// Restores default cursors through instance call.
    bool restore() noexcept {
        return restore_defaults();
    }

    /// Evaluates and applies the composite state to the operating system hardware cursor plane.
    /// Master handles remain in the internal cache and are never passed directly to user32.SetSystemCursor.
    /// In dry_run mode, composites the handles and duplicates them without modifying system slots.
    bool apply_state(const CompositeState& state, bool dry_run = false) {
        struct TargetSlot {
            uint32_t ocr_id;
            BaseShape shape;
        };

        std::vector<TargetSlot> targets;
        targets.reserve(3);
        targets.push_back({OCR_NORMAL, state.base_shape});

        if (multi_slot_sync_) {
            targets.push_back({OCR_IBEAM, BaseShape::TextIBeam});
            targets.push_back({OCR_HAND, BaseShape::HandPoint});
        }

        bool all_ok = true;

        for (const auto& target : targets) {
            CompositeState target_state = state;
            target_state.base_shape = target.shape;

            HCURSOR hMaster = nullptr;
            {
                std::lock_guard<std::mutex> lock(mutex_);
                hMaster = get_or_create_master_cursor_locked(target_state);
            }

            if (!hMaster) {
                all_ok = false;
                continue;
            }

            HCURSOR hCopy = duplicate_cursor_for_system(hMaster);
            if (!hCopy) {
                all_ok = false;
                continue;
            }

            if (dry_run) {
                DestroyIcon(hCopy);
                continue;
            }

            const BOOL ok = SetSystemCursor(hCopy, target.ocr_id);
            if (!ok) {
                DestroyIcon(hCopy);
                all_ok = false;
            }
        }

        return all_ok;
    }

    /// Configures whether dynamic visual states synchronize across multiple system cursor slots.
    void set_multi_slot_sync(bool enabled) noexcept {
        multi_slot_sync_ = enabled;
    }

    /// Returns whether multi-slot synchronization is active.
    [[nodiscard]] bool multi_slot_sync() const noexcept {
        return multi_slot_sync_;
    }

    /// Clears all cached master cursor handles.
    void clear_cache() noexcept {
        std::lock_guard<std::mutex> lock(mutex_);
        cache_map_.clear();
        lru_order_.clear();
    }

    /// Returns the number of master handles currently cached.
    [[nodiscard]] size_t cached_count() const noexcept {
        std::lock_guard<std::mutex> lock(mutex_);
        return cache_map_.size();
    }

    /// Checks if a state hash exists in the cache.
    [[nodiscard]] bool contains_hash(uint64_t hash) const noexcept {
        std::lock_guard<std::mutex> lock(mutex_);
        return cache_map_.find(hash) != cache_map_.end();
    }

    /// Returns the maximum capacity of the LRU cache.
    [[nodiscard]] size_t capacity() const noexcept {
        return capacity_;
    }

    /// Returns the active canvas size for procedural composition.
    [[nodiscard]] int canvas_size() const noexcept {
        return canvas_size_;
    }

private:
    struct CacheEntry {
        UniqueCursor cursor;
        std::list<uint64_t>::iterator order_it;
    };

    HCURSOR get_or_create_master_cursor_locked(const CompositeState& state) {
        const uint64_t state_hash = state.compute_hash();
        auto it = cache_map_.find(state_hash);
        if (it != cache_map_.end()) {
            lru_order_.erase(it->second.order_it);
            lru_order_.push_front(state_hash);
            it->second.order_it = lru_order_.begin();
            return it->second.cursor.get();
        }

        UniqueCursor master_cursor = BitmapCompositor::compose(state, canvas_size_);
        if (!master_cursor) {
            return nullptr;
        }
        HCURSOR hMaster = master_cursor.get();

        if (cache_map_.size() >= capacity_) {
            const uint64_t oldest_hash = lru_order_.back();
            lru_order_.pop_back();
            cache_map_.erase(oldest_hash);
        }

        lru_order_.push_front(state_hash);
        cache_map_.emplace(
            state_hash,
            CacheEntry{std::move(master_cursor), lru_order_.begin()}
        );
        return hMaster;
    }

    int canvas_size_{kDefaultCanvasSize};
    size_t capacity_{kDefaultCapacity};
    bool multi_slot_sync_{true};
    mutable std::mutex mutex_;
    std::unordered_map<uint64_t, CacheEntry> cache_map_;
    std::list<uint64_t> lru_order_;
};

/// RAII guard that restores default Windows cursors upon destruction.
struct ScopedCursorRestorer {
    ScopedCursorRestorer() = default;

    ~ScopedCursorRestorer() noexcept {
        HardwareCursorDriver::restore_defaults();
    }

    ScopedCursorRestorer(const ScopedCursorRestorer&) = delete;
    ScopedCursorRestorer& operator=(const ScopedCursorRestorer&) = delete;
    ScopedCursorRestorer(ScopedCursorRestorer&&) noexcept = default;
    ScopedCursorRestorer& operator=(ScopedCursorRestorer&&) noexcept = default;
};

} // namespace dynamic_cursor

#endif // DYNAMIC_CURSOR_HARDWARE_DRIVER_HPP
