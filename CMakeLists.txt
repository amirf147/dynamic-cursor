# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Amir Farhadi

cmake_minimum_required(VERSION 3.31)

project(dynamic-cursor LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(MSVC)
    add_compile_options(/O2 /EHsc /W4 /WX /permissive- /utf-8 /GL)
    add_link_options(/LTCG)
else()
    add_compile_options(-O2 -std=c++20 -Wall -Wextra -Werror)
endif()

include_directories(${CMAKE_CURRENT_SOURCE_DIR}/src/core/include)

add_executable(dynamic-cursor-tray WIN32
    src/daemon/main.cpp
)

target_link_libraries(dynamic-cursor-tray PRIVATE
    user32
    gdi32
    shell32
)

add_executable(test_native_core
    tests/test_native_core.cpp
)

target_link_libraries(test_native_core PRIVATE
    user32
    gdi32
    shell32
    psapi
)

enable_testing()
add_test(NAME test_native_core COMMAND test_native_core)
