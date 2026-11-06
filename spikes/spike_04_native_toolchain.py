# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Amir Farhadi
"""
Native Toolchain Scout & Win32 C++20 Compilation Spike

Discovers host compilers (MSVC, Clang, GCC), Windows SDKs, build utilities (CMake, Ninja),
and Windhawk mod prerequisites. Compiles a minimal C++20 Win32 test binary to measure
handle creation latency, memory working set, and binary footprint, cleaning up all
binary artifacts immediately.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import winreg
from typing import Any, Dict, List, Optional


def sanitize_path(path_str: Optional[str]) -> Optional[str]:
    """Sanitize user-specific path components to maintain public repository safety."""
    if not path_str:
        return None
    userprofile = os.environ.get("USERPROFILE", "")
    username = os.environ.get("USERNAME", "")
    localappdata = os.environ.get("LOCALAPPDATA", "")
    appdata = os.environ.get("APPDATA", "")

    sanitized = path_str
    if localappdata and localappdata.lower() in sanitized.lower():
        sanitized = sanitized.replace(localappdata, "%LOCALAPPDATA%")
    if appdata and appdata.lower() in sanitized.lower():
        sanitized = sanitized.replace(appdata, "%APPDATA%")
    if userprofile and userprofile.lower() in sanitized.lower():
        sanitized = sanitized.replace(userprofile, "%USERPROFILE%")
    if username and username in sanitized:
        sanitized = sanitized.replace(username, "%USERNAME%")
    return sanitized


def find_vswhere() -> Optional[Path]:
    """Locate vswhere.exe in standard Visual Studio installer locations."""
    prog_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    vswhere_path = Path(prog_files_x86) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    if vswhere_path.is_file():
        return vswhere_path
    from_path = shutil.which("vswhere")
    return Path(from_path) if from_path else None


def probe_visual_studio() -> Dict[str, Any]:
    """Inspect Visual Studio / MSVC installation and Windows SDK."""
    result: Dict[str, Any] = {
        "installed": False,
        "vswhere_path": None,
        "installation_path": None,
        "display_name": None,
        "version": None,
        "vcvars64_bat": None,
        "cl_path": None,
        "cmake_path": None,
        "ninja_path": None,
        "windows_sdk_version": None,
        "windows_sdk_dir": None,
    }

    vswhere = find_vswhere()
    if not vswhere:
        return result

    result["vswhere_path"] = sanitize_path(str(vswhere))
    try:
        proc = subprocess.run(
            [str(vswhere), "-latest", "-format", "json"],
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(proc.stdout)
        if not data:
            return result

        vs_info = data[0]
        install_path = Path(vs_info.get("installationPath", ""))
        result["installed"] = True
        result["installation_path"] = sanitize_path(str(install_path))
        result["display_name"] = vs_info.get("displayName")
        result["version"] = vs_info.get("installationVersion")

        vcvars64 = install_path / "VC" / "Auxiliary" / "Build" / "vcvars64.bat"
        if vcvars64.is_file():
            result["vcvars64_bat"] = sanitize_path(str(vcvars64))

        # Query vcvars64 environment to obtain cl, cmake, ninja, and Windows SDK paths
        cmd = f'cmd.exe /c ""{vcvars64}" > nul && where cl.exe cmake.exe ninja.exe && set WindowsSDKVersion && set WindowsSdkDir"'
        probe_proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
        )
        if probe_proc.returncode == 0:
            lines = probe_proc.stdout.strip().splitlines()
            for line in lines:
                line_clean = line.strip()
                if line_clean.lower().endswith("cl.exe") and not result["cl_path"]:
                    result["cl_path"] = sanitize_path(line_clean)
                elif line_clean.lower().endswith("cmake.exe") and not result["cmake_path"]:
                    result["cmake_path"] = sanitize_path(line_clean)
                elif line_clean.lower().endswith("ninja.exe") and not result["ninja_path"]:
                    result["ninja_path"] = sanitize_path(line_clean)
                elif line_clean.startswith("WindowsSDKVersion="):
                    result["windows_sdk_version"] = line_clean.split("=", 1)[1].strip().rstrip("\\")
                elif line_clean.startswith("WindowsSdkDir="):
                    result["windows_sdk_dir"] = sanitize_path(line_clean.split("=", 1)[1].strip())
    except Exception as exc:
        result["error"] = str(exc)

    return result


def probe_path_toolchains() -> Dict[str, Optional[str]]:
    """Check PATH for compilers and build utilities."""
    targets = ["cl", "clang", "clang++", "clang-cl", "gcc", "g++", "cmake", "ninja"]
    found: Dict[str, Optional[str]] = {}
    for target in targets:
        path = shutil.which(target)
        found[target] = sanitize_path(path) if path else None
    return found


def probe_windhawk() -> Dict[str, Any]:
    """Inspect Windhawk installation, compiler, and mod development prerequisites."""
    result: Dict[str, Any] = {
        "installed": False,
        "install_dir": None,
        "engine_dir": None,
        "compiler_dir": None,
        "clang_version": None,
        "windhawk_api_header": False,
        "compile_flags_present": False,
        "compile_flags_content": None,
    }

    windhawk_dir: Optional[Path] = None

    # Check registry
    for root_key, subkey in [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Windhawk"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Windhawk"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Windhawk"),
    ]:
        try:
            with winreg.OpenKey(root_key, subkey) as key:
                val, _ = winreg.QueryValueEx(key, "install_dir")
                if val and Path(val).is_dir():
                    windhawk_dir = Path(val)
                    break
        except OSError:
            pass

    # Check filesystem fallback
    if not windhawk_dir:
        prog_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        candidate = prog_files / "Windhawk"
        if candidate.is_dir():
            windhawk_dir = candidate

    if not windhawk_dir:
        return result

    result["installed"] = True
    result["install_dir"] = sanitize_path(str(windhawk_dir))

    engine_dir = windhawk_dir / "Engine"
    if engine_dir.is_dir():
        result["engine_dir"] = sanitize_path(str(engine_dir))

    compiler_dir = windhawk_dir / "Compiler"
    if compiler_dir.is_dir():
        result["compiler_dir"] = sanitize_path(str(compiler_dir))
        clang_bin = compiler_dir / "bin" / "clang++.exe"
        if clang_bin.is_file():
            try:
                proc = subprocess.run([str(clang_bin), "--version"], capture_output=True, text=True, check=True)
                first_line = proc.stdout.splitlines()[0] if proc.stdout else ""
                result["clang_version"] = first_line.strip()
            except Exception:
                result["clang_version"] = "Detected"

        api_header = compiler_dir / "include" / "windhawk_api.h"
        result["windhawk_api_header"] = api_header.is_file()

        flags_file = compiler_dir / "compile_flags.txt"
        if flags_file.is_file():
            result["compile_flags_present"] = True
            try:
                result["compile_flags_content"] = flags_file.read_text(encoding="utf-8").strip().splitlines()
            except Exception:
                pass

    return result


def execute_msvc_compilation_spike(repo_root: Path, vs_info: Dict[str, Any]) -> Dict[str, Any]:
    """Compile and execute the minimal C++20 Win32 test binary via MSVC."""
    cpp_source = repo_root / "spikes" / "spike_04_test.cpp"
    exe_target = repo_root / "spikes" / "spike_04_test_msvc.exe"
    obj_target = repo_root / "spikes" / "spike_04_test_msvc.obj"

    telemetry: Dict[str, Any] = {
        "compiler": "MSVC",
        "compiled": False,
        "binary_size_bytes": None,
        "runtime_telemetry": None,
        "cleanup_successful": False,
    }

    if not cpp_source.is_file():
        telemetry["error"] = "spikes/spike_04_test.cpp not found"
        return telemetry

    vcvars64 = vs_info.get("vcvars64_bat")
    if not vcvars64:
        telemetry["error"] = "vcvars64.bat not found in Visual Studio installation"
        return telemetry

    actual_vcvars64 = os.path.expandvars(vcvars64)
    compile_cmd = (
        f'cmd.exe /c ""{actual_vcvars64}" > nul && '
        f'cl /nologo /O2 /std:c++20 /GL /EHsc /Fo:"{obj_target}" "{cpp_source}" /Fe:"{exe_target}" '
        f'user32.lib gdi32.lib /link /LTCG /OPT:REF /OPT:ICF"'
    )

    try:
        build_proc = subprocess.run(
            compile_cmd,
            capture_output=True,
            text=True,
        )
        if build_proc.returncode != 0:
            telemetry["error"] = f"Compilation failed: {build_proc.stderr or build_proc.stdout}"
            return telemetry

        if exe_target.is_file():
            telemetry["compiled"] = True
            telemetry["binary_size_bytes"] = exe_target.stat().st_size

            run_proc = subprocess.run(
                [str(exe_target)],
                capture_output=True,
                text=True,
            )
            if run_proc.returncode == 0:
                try:
                    telemetry["runtime_telemetry"] = json.loads(run_proc.stdout)
                except Exception:
                    telemetry["runtime_telemetry"] = {"raw": run_proc.stdout.strip()}
            else:
                telemetry["error"] = f"Execution failed: {run_proc.stderr}"
    finally:
        for artifact in [exe_target, obj_target]:
            if artifact.is_file():
                try:
                    artifact.unlink()
                except OSError:
                    pass
        telemetry["cleanup_successful"] = not exe_target.is_file() and not obj_target.is_file()

    return telemetry


def execute_clang_compilation_spike(repo_root: Path, wh_info: Dict[str, Any]) -> Dict[str, Any]:
    """Compile and execute the minimal C++20 Win32 test binary via Windhawk Clang."""
    cpp_source = repo_root / "spikes" / "spike_04_test.cpp"
    exe_target = repo_root / "spikes" / "spike_04_test_clang.exe"

    telemetry: Dict[str, Any] = {
        "compiler": "Windhawk Clang (MinGW-w64)",
        "compiled": False,
        "binary_size_bytes": None,
        "runtime_telemetry": None,
        "cleanup_successful": False,
    }

    compiler_dir = wh_info.get("compiler_dir")
    if not compiler_dir:
        telemetry["error"] = "Windhawk compiler directory not found"
        return telemetry

    actual_compiler_dir = Path(os.path.expandvars(compiler_dir))
    clang_bin = actual_compiler_dir / "bin" / "clang++.exe"
    if not clang_bin.is_file():
        telemetry["error"] = "clang++.exe not found in Windhawk compiler directory"
        return telemetry

    compile_cmd = [
        str(clang_bin),
        "-O2",
        "-std=c++20",
        "-target",
        "x86_64-w64-mingw32",
        "-static",
        str(cpp_source),
        "-o",
        str(exe_target),
        "-luser32",
        "-lgdi32",
        "-lpsapi",
    ]

    try:
        build_proc = subprocess.run(
            compile_cmd,
            capture_output=True,
            text=True,
        )
        if build_proc.returncode != 0:
            telemetry["error"] = f"Compilation failed: {build_proc.stderr or build_proc.stdout}"
            return telemetry

        if exe_target.is_file():
            telemetry["compiled"] = True
            telemetry["binary_size_bytes"] = exe_target.stat().st_size

            run_proc = subprocess.run(
                [str(exe_target)],
                capture_output=True,
                text=True,
            )
            if run_proc.returncode == 0:
                try:
                    telemetry["runtime_telemetry"] = json.loads(run_proc.stdout)
                except Exception:
                    telemetry["runtime_telemetry"] = {"raw": run_proc.stdout.strip()}
            else:
                telemetry["error"] = f"Execution failed: {run_proc.stderr}"
    finally:
        if exe_target.is_file():
            try:
                exe_target.unlink()
            except OSError:
                pass
        telemetry["cleanup_successful"] = not exe_target.is_file()

    return telemetry


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent

    print("=== Subagent 4: Native Toolchain & Windhawk Scout ===")
    print("1. Probing Visual Studio & Windows SDK...")
    vs_info = probe_visual_studio()
    print(f"   Visual Studio installed: {vs_info['installed']}")
    if vs_info["installed"]:
        print(f"   Display Name: {vs_info['display_name']}")
        print(f"   Version: {vs_info['version']}")
        print(f"   Windows SDK: {vs_info['windows_sdk_version']}")
        print(f"   Compiler (cl.exe): {vs_info['cl_path']}")
        print(f"   CMake: {vs_info['cmake_path']}")
        print(f"   Ninja: {vs_info['ninja_path']}")

    print("\n2. Probing PATH toolchains...")
    path_tools = probe_path_toolchains()
    for tool, loc in path_tools.items():
        print(f"   {tool}: {loc or 'Not on PATH'}")

    print("\n3. Probing Windhawk installation...")
    wh_info = probe_windhawk()
    print(f"   Windhawk installed: {wh_info['installed']}")
    if wh_info["installed"]:
        print(f"   Install Directory: {wh_info['install_dir']}")
        print(f"   Bundled Clang: {wh_info['clang_version']}")
        print(f"   windhawk_api.h available: {wh_info['windhawk_api_header']}")
        print(f"   compile_flags.txt available: {wh_info['compile_flags_present']}")

    print("\n4. Running Minimal C++20 Win32 Compilation Spike (MSVC)...")
    msvc_telemetry = execute_msvc_compilation_spike(repo_root, vs_info)
    print(f"   Compiled successfully: {msvc_telemetry.get('compiled')}")
    print(f"   Binary size: {msvc_telemetry.get('binary_size_bytes')} bytes")
    print(f"   Artifact cleanup successful: {msvc_telemetry.get('cleanup_successful')}")
    if msvc_telemetry.get("runtime_telemetry"):
        rt = msvc_telemetry["runtime_telemetry"]
        print(f"   Iterations: {rt.get('iterations')}")
        print(f"   Avg handle creation latency: {rt.get('avg_creation_us'):.2f} us")
        print(f"   Working set: {rt.get('working_set_bytes') / (1024 * 1024):.2f} MB")
        print(f"   Peak working set: {rt.get('peak_working_set_bytes') / (1024 * 1024):.2f} MB")
        print(f"   Private bytes: {rt.get('private_bytes') / (1024 * 1024):.2f} MB")

    print("\n5. Running Minimal C++20 Win32 Compilation Spike (Windhawk Clang)...")
    clang_telemetry = execute_clang_compilation_spike(repo_root, wh_info)
    print(f"   Compiled successfully: {clang_telemetry.get('compiled')}")
    print(f"   Binary size: {clang_telemetry.get('binary_size_bytes')} bytes")
    print(f"   Artifact cleanup successful: {clang_telemetry.get('cleanup_successful')}")
    if clang_telemetry.get("runtime_telemetry"):
        rt = clang_telemetry["runtime_telemetry"]
        print(f"   Iterations: {rt.get('iterations')}")
        print(f"   Avg handle creation latency: {rt.get('avg_creation_us'):.2f} us")
        print(f"   Working set: {rt.get('working_set_bytes') / (1024 * 1024):.2f} MB")
        print(f"   Peak working set: {rt.get('peak_working_set_bytes') / (1024 * 1024):.2f} MB")
        print(f"   Private bytes: {rt.get('private_bytes') / (1024 * 1024):.2f} MB")

    full_report = {
        "visual_studio": vs_info,
        "path_toolchains": path_tools,
        "windhawk": wh_info,
        "msvc_spike": msvc_telemetry,
        "clang_spike": clang_telemetry,
    }

    print("\n--- Structured Telemetry Output (JSON) ---")
    print(json.dumps(full_report, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
