// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Amir Farhadi

#include <windows.h>
#include <psapi.h>
#include <chrono>
#include <cstdint>
#include <iostream>
#include <vector>

#pragma comment(lib, "user32.lib")
#pragma comment(lib, "gdi32.lib")

static HCURSOR CreateTestCursor(int width, int height) {
    BITMAPINFO bmi = {};
    bmi.bmiHeader.biSize = sizeof(BITMAPINFOHEADER);
    bmi.bmiHeader.biWidth = width;
    bmi.bmiHeader.biHeight = -height;
    bmi.bmiHeader.biPlanes = 1;
    bmi.bmiHeader.biBitCount = 32;
    bmi.bmiHeader.biCompression = BI_RGB;

    uint32_t* pixelBuffer = nullptr;
    HDC hdcScreen = GetDC(nullptr);
    HBITMAP hColorDIB = CreateDIBSection(
        hdcScreen,
        &bmi,
        DIB_RGB_COLORS,
        reinterpret_cast<void**>(&pixelBuffer),
        nullptr,
        0
    );
    ReleaseDC(nullptr, hdcScreen);

    if (!hColorDIB || !pixelBuffer) {
        if (hColorDIB) {
            DeleteObject(hColorDIB);
        }
        return nullptr;
    }

    const size_t totalPixels = static_cast<size_t>(width * height);
    for (size_t i = 0; i < totalPixels; ++i) {
        pixelBuffer[i] = 0xFF00FFFF;
    }

    const int maskPitch = ((width + 31) / 32) * 4;
    std::vector<uint8_t> maskBits(static_cast<size_t>(maskPitch * height), 0);
    HBITMAP hMask = CreateBitmap(width, height, 1, 1, maskBits.data());
    if (!hMask) {
        DeleteObject(hColorDIB);
        return nullptr;
    }

    ICONINFO iconInfo = {};
    iconInfo.fIcon = FALSE;
    iconInfo.xHotspot = 0;
    iconInfo.yHotspot = 0;
    iconInfo.hbmMask = hMask;
    iconInfo.hbmColor = hColorDIB;

    HCURSOR hCursor = CreateIconIndirect(&iconInfo);

    DeleteObject(hColorDIB);
    DeleteObject(hMask);

    return hCursor;
}

int main() {
    constexpr int ITERATIONS = 5000;
    constexpr int WIDTH = 32;
    constexpr int HEIGHT = 32;

    HCURSOR hInitial = CreateTestCursor(WIDTH, HEIGHT);
    if (!hInitial) {
        std::cerr << "{\"error\": \"Failed to create initial test cursor handle\"}" << std::endl;
        return 1;
    }
    DestroyIcon(hInitial);

    auto start = std::chrono::high_resolution_clock::now();
    for (int i = 0; i < ITERATIONS; ++i) {
        HCURSOR h = CreateTestCursor(WIDTH, HEIGHT);
        if (h) {
            DestroyIcon(h);
        }
    }
    auto end = std::chrono::high_resolution_clock::now();

    const std::chrono::duration<double, std::milli> elapsed = end - start;
    const double total_ms = elapsed.count();
    const double avg_us = (total_ms * 1000.0) / ITERATIONS;

    PROCESS_MEMORY_COUNTERS_EX pmc = {};
    GetProcessMemoryInfo(
        GetCurrentProcess(),
        reinterpret_cast<PROCESS_MEMORY_COUNTERS*>(&pmc),
        sizeof(pmc)
    );

    std::cout << "{\n";
    std::cout << "  \"iterations\": " << ITERATIONS << ",\n";
    std::cout << "  \"total_time_ms\": " << total_ms << ",\n";
    std::cout << "  \"avg_creation_us\": " << avg_us << ",\n";
    std::cout << "  \"working_set_bytes\": " << pmc.WorkingSetSize << ",\n";
    std::cout << "  \"peak_working_set_bytes\": " << pmc.PeakWorkingSetSize << ",\n";
    std::cout << "  \"private_bytes\": " << pmc.PrivateUsage << ",\n";
    std::cout << "  \"success\": true\n";
    std::cout << "}\n";

    return 0;
}
