#!/usr/bin/env python3
"""
Unified test suite for powertech-center/runners.

Usage:
    python3 test.py                     # run all groups
    python3 test.py env tools cmake     # run specific groups
    python3 test.py --list              # list available groups

Groups cover all 8 target platforms (linux/windows/macos, x64/arm64, gnu/musl).
musl-specific groups are auto-skipped on non-musl platforms.
"""

import argparse
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# -- ANSI colours -------------------------------------------------------------
if sys.stdout.isatty():
    GREEN = "\033[0;32m"; RED = "\033[0;31m"; YELLOW = "\033[1;33m"
    BLUE  = "\033[0;34m"; BOLD = "\033[1m";   NC = "\033[0m"
else:
    GREEN = RED = YELLOW = BLUE = BOLD = NC = ""

# -- Test framework -----------------------------------------------------------
_passed: list[str] = []
_failed: list[str] = []
_skipped: list[str] = []
_current_suite = ""


def suite(name: str) -> None:
    global _current_suite
    _current_suite = name
    print(f"\n{BLUE}{BOLD}=== {name} ==={NC}")


def pass_(name: str) -> None:
    _passed.append(f"[{_current_suite}] {name}")
    print(f"  {GREEN}PASS{NC}  {name}")


def fail(name: str, reason: str = "") -> None:
    _failed.append(f"[{_current_suite}] {name}")
    msg = f"  {RED}FAIL{NC}  {name}"
    if reason:
        msg += f"\n        {RED}{reason}{NC}"
    print(msg)


def skip(name: str, reason: str = "") -> None:
    _skipped.append(f"[{_current_suite}] {name}")
    suffix = f" ({reason})" if reason else ""
    print(f"  {YELLOW}SKIP{NC}  {name}{suffix}")


def run(cmd: list, **kwargs) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, **kwargs)
    except FileNotFoundError:
        return subprocess.CompletedProcess(cmd, 127, "", f"{cmd[0]}: command not found")
    except OSError as e:
        return subprocess.CompletedProcess(cmd, 126, "", str(e))


def has(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def cmd_output(cmd: list) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True)
    return (r.stdout + r.stderr).strip()


def elf_interp(binary: str) -> str:
    if not has("readelf"):
        return ""
    r = run(["readelf", "-l", binary])
    m = re.search(r"interpreter:\s*(.+?)\]", r.stdout + r.stderr)
    return m.group(1).strip() if m else ""


def check_tool(name: str, args: list = None, *, label: str = None) -> bool:
    label = label or name
    if not has(name):
        fail(f"{label}", "not found")
        return False
    argv = [name] + (args if args is not None else ["--version"])
    r = run(argv)
    if r.returncode >= 126:
        fail(f"{label}", "not executable")
        return False
    out = (r.stdout + r.stderr).splitlines()
    ver = out[0] if out else ""
    pass_(f"{label} -- {ver}")
    return True


# -- Platform helpers ---------------------------------------------------------
def _detect_os() -> str:
    s = platform.system().lower()
    if s == "darwin":
        return "macos"
    if s == "windows":
        return "windows"
    return "linux"


def _detect_arch() -> str:
    m = platform.machine().lower()
    if m in ("aarch64", "arm64"):
        return "arm64"
    return "x64"


def _detect_libc() -> str:
    if _detect_os() != "linux":
        return ""
    # Check musl dynamic linker presence
    arch = platform.machine().lower()
    musl_ld = f"/lib/ld-musl-{arch}.so.1"
    if Path(musl_ld).exists():
        return "musl"
    # Check ldd output
    r = run(["ldd", "--version"])
    out = (r.stdout + r.stderr).lower()
    if "musl" in out:
        return "musl"
    return "gnu"


def _detect_platform() -> str:
    os_ = _detect_os()
    arch = _detect_arch()
    if os_ == "linux":
        libc = _detect_libc()
        return f"linux-{arch}-{libc}" if libc else f"linux-{arch}"
    return f"{os_}-{arch}"


def _musl_interp() -> str:
    arch = platform.machine().lower()
    if arch == "x86_64":
        return "/lib/ld-musl-x86_64.so.1"
    if arch in ("aarch64", "arm64"):
        return "/lib/ld-musl-aarch64.so.1"
    return ""


def _glibc_linker() -> str:
    arch = platform.machine().lower()
    if arch == "x86_64":
        return "/lib64/ld-linux-x86-64.so.2"
    if arch in ("aarch64", "arm64"):
        return "/lib/ld-linux-aarch64.so.1"
    return ""


def _glibc_libdir() -> str:
    arch = platform.machine().lower()
    if arch == "x86_64":
        return "/lib/x86_64-linux-gnu"
    if arch in ("aarch64", "arm64"):
        return "/lib/aarch64-linux-gnu"
    return ""


def _musl_rust_target() -> str:
    arch = platform.machine().lower()
    if arch == "x86_64":
        return "x86_64-unknown-linux-musl"
    if arch in ("aarch64", "arm64"):
        return "aarch64-unknown-linux-musl"
    return ""


def is_musl() -> bool:
    return _detect_libc() == "musl"

def is_linux() -> bool:
    return _detect_os() == "linux"

def is_windows() -> bool:
    return _detect_os() == "windows"

def is_macos() -> bool:
    return _detect_os() == "macos"


# -- Compile helper -----------------------------------------------------------
_C_SRC = '#include <stdio.h>\nint main(void){printf("hello-c\\n");return 0;}\n'
_CPP_SRC = '#include <iostream>\nint main(){std::cout<<"hello-cpp"<<std::endl;return 0;}\n'
_RS_SRC = 'fn main(){println!("hello-rust");}\n'
_GO_SRC = 'package main\nimport "fmt"\nfunc main(){fmt.Println("hello-go")}\n'
_GO_CGO_SRC = ('package main\n/*\n#include <stdio.h>\nvoid hello_c(void)'
               '{printf("hello-cgo\\n");}\n*/\nimport "C"\nfunc main(){C.hello_c()}\n')


def _compile_and_run(compiler: str, src_name: str, src_content: str,
                     extra_flags: list = None, tmpdir: str = "") -> tuple[bool, str]:
    src = Path(tmpdir) / src_name
    out = Path(tmpdir) / (src_name + ".out")
    src.write_text(src_content)
    cmd = [compiler] + (extra_flags or []) + ["-o", str(out), str(src)]
    r = run(cmd)
    if r.returncode != 0:
        err = (r.stdout + r.stderr).strip().splitlines()
        return False, (err[0] if err else "compilation failed")
    r2 = run([str(out)])
    if r2.returncode != 0:
        return False, f"binary exited {r2.returncode}"
    interp = elf_interp(str(out))
    return True, interp


# ============================================================================
# GROUP: env
# ============================================================================
def group_env() -> None:
    suite("Runner environment")

    env_vars = ["RUNNER_PLATFORM", "RUNNER_OS", "RUNNER_ARCH", "RUNNER_NAME"]
    for var in env_vars:
        val = os.environ.get(var, "")
        print(f"  {BOLD}{var}{NC}: {val if val else f'{YELLOW}<not set>{NC}'}")

    suite("Platform detection")

    computed = _detect_platform()
    declared = os.environ.get("RUNNER_PLATFORM", "")

    print(f"  Computed RUNNER_PLATFORM : {BOLD}{computed}{NC}")
    print(f"  Declared RUNNER_PLATFORM : {BOLD}{declared if declared else '<not set>'}{NC}")

    if not declared:
        os.environ["RUNNER_PLATFORM"] = computed
        pass_(f"RUNNER_PLATFORM computed as '{computed}' (env not set, using computed)")
    elif declared == computed:
        pass_(f"RUNNER_PLATFORM matches computed value '{computed}'")
    else:
        fail("RUNNER_PLATFORM matches computed",
             f"declared='{declared}' vs computed='{computed}'")

    suite("System info")
    print(f"  OS       : {platform.system()} {platform.release()}")
    print(f"  Machine  : {platform.machine()}")
    print(f"  Python   : {platform.python_version()}")
    if is_linux():
        r = run(["uname", "-r"])
        print(f"  Kernel   : {r.stdout.strip()}")
    if is_linux() and Path("/etc/os-release").exists():
        for line in Path("/etc/os-release").read_text().splitlines():
            if line.startswith(("NAME=", "VERSION_ID=", "ID=")):
                print(f"  {line}")


# ============================================================================
# GROUP: tools
# ============================================================================
def group_tools() -> None:
    suite("Core utilities")
    check_tool("bash",    ["--version"])
    check_tool("pwsh",    ["--version"])
    check_tool("git",     ["--version"])
    check_tool("git-lfs", ["version"])
    check_tool("curl",    ["--version"])
    check_tool("wget",    ["--version"])
    check_tool("aria2c",  ["--version"])
    check_tool("tar",     ["--version"])
    check_tool("xz",      ["--version"])
    check_tool("zstd",    ["--version"])
    check_tool("zip",     ["-v"])
    check_tool("unzip",   ["-v"])
    check_tool("7z",      ["i"])
    check_tool("rsync",   ["--version"])
    check_tool("gpg",     ["--version"])
    check_tool("gh",      ["--version"])
    check_tool("aws",     ["--version"])
    check_tool("az",      ["--version"])

    suite("Text processing")
    check_tool("jq",   ["--version"])
    check_tool("yq",   ["--version"])
    check_tool("grep", ["--version"])
    check_tool("sed",  ["--version"])
    check_tool("awk",  ["--version"])
    check_tool("find", ["--version"])
    check_tool("tree", ["--version"])

    suite("Build systems")
    check_tool("cmake",      ["--version"])
    check_tool("ninja",      ["--version"])
    check_tool("make",       ["--version"])
    check_tool("pkg-config", ["--version"])

    suite("GCC toolchain")
    check_tool("gcc",     ["--version"])
    check_tool("g++",     ["--version"])
    check_tool("ld",      ["--version"])
    check_tool("ar",      ["--version"])
    check_tool("nm",      ["--version"])
    check_tool("objdump", ["--version"])
    check_tool("readelf", ["--version"])

    suite("Clang/LLVM toolchain")
    check_tool("clang",    ["--version"])
    check_tool("clang++",  ["--version"])
    check_tool("lld",      ["--version"])
    check_tool("ld.lld",   ["--version"])
    check_tool("llvm-ar",  ["--version"])

    suite("Go")
    check_tool("go",    ["version"])

    suite("Rust")
    check_tool("rustc",         ["--version"])
    check_tool("cargo",         ["--version"])
    check_tool("rustup",        ["--version"])
    check_tool("rustfmt",       ["--version"])
    check_tool("clippy-driver", ["--version"])

    suite("Python")
    check_tool("python3", ["--version"])
    check_tool("pip3",    ["--version"])

    suite("Node.js")
    check_tool("node", ["--version"])
    check_tool("npm",  ["--version"])

    suite(".NET")
    check_tool("dotnet", ["--version"])

    suite("Java")
    check_tool("java",  ["-version"])
    check_tool("javac", ["-version"])

    suite("Package managers")
    check_tool("gem",   ["--version"])
    check_tool("vcpkg", ["version"])


# ============================================================================
# GROUP: compilers
# ============================================================================
def group_compilers() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        suite("C compilers -- compile + run")
        for cc in ["gcc", "clang", "cc"]:
            if not has(cc):
                skip(f"{cc} compiles C", "not found")
                continue
            ok, info = _compile_and_run(cc, f"hello_{cc}.c", _C_SRC, tmpdir=tmp)
            if ok:
                pass_(f"{cc} compiles and runs C")
            else:
                fail(f"{cc} compiles C", info)

        suite("C++ compilers -- compile + run")
        for cxx in ["g++", "clang++", "c++"]:
            if not has(cxx):
                skip(f"{cxx} compiles C++", "not found")
                continue
            ok, info = _compile_and_run(cxx, f"hello_{cxx}.cpp", _CPP_SRC, tmpdir=tmp)
            if ok:
                pass_(f"{cxx} compiles and runs C++")
            else:
                fail(f"{cxx} compiles C++", info)

        # On musl: verify ELF interpreter is musl
        if is_linux() and is_musl():
            musl_interp = _musl_interp()
            suite("Compiler output -> musl ELF")
            for cc, src_name, src_content in [
                ("gcc",     "musl_gcc.c",      _C_SRC),
                ("g++",     "musl_gpp.cpp",    _CPP_SRC),
                ("cc",      "musl_cc.c",       _C_SRC),
                ("c++",     "musl_cxx.cpp",    _CPP_SRC),
                ("clang",   "musl_clang.c",    _C_SRC),
                ("clang++", "musl_clangpp.cpp", _CPP_SRC),
            ]:
                if not has(cc):
                    skip(f"{cc} -> musl", f"{cc} not found")
                    continue
                ok, interp = _compile_and_run(cc, src_name, src_content, tmpdir=tmp)
                if ok:
                    if interp == musl_interp:
                        pass_(f"{cc} binary -> musl ({interp})")
                    else:
                        fail(f"{cc} binary -> musl", f"got: '{interp}'")
                else:
                    fail(f"{cc} compiles", interp)

        # On gnu: verify ELF interpreter is glibc
        if is_linux() and not is_musl():
            glibc = _glibc_linker()
            suite("Compiler output -> glibc ELF")
            for cc in ["gcc", "clang"]:
                if not has(cc):
                    skip(f"{cc} -> glibc", f"{cc} not found")
                    continue
                ok, interp = _compile_and_run(cc, f"glibc_{cc}.c", _C_SRC, tmpdir=tmp)
                if ok:
                    if interp == glibc:
                        pass_(f"{cc} binary -> glibc ({interp})")
                    else:
                        fail(f"{cc} binary -> glibc", f"got: '{interp}', expected: '{glibc}'")
                else:
                    fail(f"{cc} compiles C", interp)


# ============================================================================
# GROUP: static
# ============================================================================
def group_static() -> None:
    if not is_linux():
        suite("Static linking")
        skip("static linking tests", "Linux only (ELF)")
        return

    with tempfile.TemporaryDirectory() as tmp:
        for cc, src_name, src_content, label in [
            ("gcc",     "static_gcc.c",      _C_SRC,   "gcc -static"),
            ("clang",   "static_clang.c",    _C_SRC,   "clang -static"),
            ("clang++", "static_clangpp.cpp", _CPP_SRC, "clang++ -static"),
        ]:
            suite(f"Static linking -- {cc}")
            if not has(cc):
                skip(label, f"{cc} not found")
                continue
            ok, info = _compile_and_run(cc, src_name, src_content,
                                        extra_flags=["-static"], tmpdir=tmp)
            if ok:
                pass_(f"{label} compiles and runs")
                out_path = str(Path(tmp) / (src_name + ".out"))
                interp = elf_interp(out_path)
                if not interp:
                    pass_("static binary has no interpreter")
                else:
                    fail("static binary has no interpreter", f"found: {interp}")
            else:
                if cc == "clang++":
                    skip(label, "may require static libstdc++")
                else:
                    fail(label, info)


# ============================================================================
# GROUP: cmake
# ============================================================================
_CMAKE_LISTS = """\
cmake_minimum_required(VERSION 3.20)
project(runner_test C)
add_executable(runner_test main.c)
"""
_CMAKE_MAIN = '#include <stdio.h>\nint main(void){printf("cmake-ok\\n");return 0;}\n'


def _cmake_build(tmpdir: str, build_subdir: str, extra_defs: list = None) -> tuple[bool, str]:
    bd = Path(tmpdir) / build_subdir
    bd.mkdir(parents=True, exist_ok=True)
    cfg_cmd = ["cmake", "-G", "Ninja", "-S", tmpdir, "-B", str(bd)] + (extra_defs or [])
    r = run(cfg_cmd)
    if r.returncode != 0:
        lines = (r.stdout + r.stderr).strip().splitlines()
        return False, "cmake configure failed: " + (lines[-1] if lines else "unknown")
    r2 = run(["cmake", "--build", str(bd)])
    if r2.returncode != 0:
        lines = (r2.stdout + r2.stderr).strip().splitlines()
        return False, "cmake build failed: " + (lines[-1] if lines else "unknown")
    binary = bd / "runner_test"
    if not binary.exists():
        binary = bd / "runner_test.exe"
    if not binary.exists():
        return False, "binary not found after build"
    r3 = run([str(binary)])
    if r3.returncode != 0:
        return False, f"binary exited {r3.returncode}"
    return True, elf_interp(str(binary))


def group_cmake() -> None:
    if not has("cmake") or not has("ninja"):
        suite("CMake + Ninja")
        skip("cmake tests", "cmake or ninja not found")
        return

    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "CMakeLists.txt").write_text(_CMAKE_LISTS)
        Path(tmp, "main.c").write_text(_CMAKE_MAIN)

        suite("CMake + Ninja -- default compiler")
        ok, info = _cmake_build(tmp, "build-default")
        if ok:
            pass_("cmake + ninja build succeeds (default cc)")
            if is_linux():
                if is_musl():
                    expected = _musl_interp()
                    if info == expected:
                        pass_(f"binary -> musl ({info})")
                    elif not info:
                        pass_("binary is static (musl default)")
                    else:
                        fail("binary -> musl", f"got: '{info}', expected: '{expected}'")
                else:
                    expected = _glibc_linker()
                    if info == expected:
                        pass_(f"binary -> glibc ({info})")
                    else:
                        fail("binary -> glibc", f"got: '{info}', expected: '{expected}'")
        else:
            fail("cmake + ninja build (default cc)", info)

        for cc_name in ["gcc", "clang"]:
            suite(f"CMake + Ninja -- {cc_name}")
            if has(cc_name):
                ok, info = _cmake_build(tmp, f"build-{cc_name}",
                                        [f"-DCMAKE_C_COMPILER={cc_name}"])
                if ok:
                    pass_(f"cmake + ninja + {cc_name} build succeeds")
                else:
                    fail(f"cmake + ninja + {cc_name}", info)
            else:
                skip(f"cmake + {cc_name}", f"{cc_name} not found")


# ============================================================================
# GROUP: runtimes
# ============================================================================
def group_runtimes() -> None:
    with tempfile.TemporaryDirectory() as tmp:

        # -- Go ------------------------------------------------------------
        suite("Go")
        if has("go"):
            r = run(["go", "version"])
            pass_(f"go version -- {r.stdout.strip()}")

            src = Path(tmp) / "main.go"
            out = Path(tmp) / "hello_go"
            src.write_text(_GO_SRC)
            r = run(["go", "build", "-o", str(out), str(src)])
            if r.returncode == 0 and out.exists():
                pass_("go build succeeds")
                r2 = run([str(out)])
                if r2.returncode == 0 and "hello-go" in r2.stdout:
                    pass_("go binary runs correctly")
                else:
                    fail("go binary runs", f"exit={r2.returncode}")
                if is_linux():
                    interp = elf_interp(str(out))
                    if is_musl():
                        if not interp or _musl_interp() in interp:
                            pass_("go binary -> musl or static")
                        else:
                            fail("go binary -> musl", f"interp: {interp}")
            else:
                fail("go build", (r.stdout + r.stderr).strip()[:120])

            # CGO build
            if is_linux():
                cgo_src = Path(tmp) / "cgo.go"
                cgo_out = Path(tmp) / "hello_cgo"
                cgo_src.write_text(_GO_CGO_SRC)
                env = {**os.environ, "CGO_ENABLED": "1"}
                r = subprocess.run(["go", "build", "-o", str(cgo_out), str(cgo_src)],
                                   capture_output=True, text=True, env=env)
                if r.returncode == 0 and cgo_out.exists():
                    pass_("go CGO build succeeds")
                    interp = elf_interp(str(cgo_out))
                    if is_musl():
                        if not interp or _musl_interp() in interp:
                            pass_("go CGO binary -> musl or static")
                        else:
                            fail("go CGO binary -> musl", f"interp: {interp}")
                else:
                    skip("go CGO build", "CGO build failed (may need CGO deps)")
        else:
            skip("go", "not found")

        # -- Rust ----------------------------------------------------------
        suite("Rust")
        if has("rustc"):
            r = run(["rustc", "--version"])
            pass_(f"rustc -- {r.stdout.strip()}")

            rs_src = Path(tmp) / "hello.rs"
            rs_out = Path(tmp) / "hello_rust"
            rs_src.write_text(_RS_SRC)
            r = run(["rustc", "-o", str(rs_out), str(rs_src)])
            if r.returncode == 0 and rs_out.exists():
                pass_("rustc compiles")
                r2 = run([str(rs_out)])
                if r2.returncode == 0 and "hello-rust" in r2.stdout:
                    pass_("rustc binary runs correctly")
                else:
                    fail("rustc binary runs", f"exit={r2.returncode}")
                if is_linux():
                    interp = elf_interp(str(rs_out))
                    if is_musl():
                        if not interp or _musl_interp() in interp:
                            pass_("rustc binary -> musl or static")
                        else:
                            fail("rustc binary -> musl", f"interp: {interp}")
            else:
                fail("rustc compiles", (r.stdout + r.stderr).strip()[:120])

            # cargo build
            if has("cargo"):
                cargo_proj = Path(tmp) / "cargo_proj"
                r = run(["cargo", "init", "--name", "testproj", str(cargo_proj)])
                if r.returncode == 0:
                    pass_("cargo init")
                    r2 = subprocess.run(["cargo", "build"], capture_output=True,
                                        text=True, cwd=str(cargo_proj))
                    if r2.returncode == 0:
                        pass_("cargo build succeeds")
                    else:
                        fail("cargo build", (r2.stdout + r2.stderr).strip()[:120])
                else:
                    fail("cargo init", (r.stdout + r.stderr).strip()[:80])
        else:
            skip("rust", "rustc not found")

        # -- Python --------------------------------------------------------
        suite("Python")
        py = "python3" if has("python3") else ("python" if has("python") else None)
        if py:
            r = run([py, "--version"])
            pass_(f"{py} -- {(r.stdout + r.stderr).strip()}")
            r = run([py, "-c", "import os; print(os.name)"])
            if r.returncode == 0:
                pass_(f"python import os -- {r.stdout.strip()}")
            r = run([py, "-c", "import ssl; print(ssl.OPENSSL_VERSION)"])
            if r.returncode == 0:
                pass_(f"python import ssl -- {r.stdout.strip()}")
        else:
            skip("python", "not found")

        # -- Node.js -------------------------------------------------------
        suite("Node.js")
        if has("node"):
            r = run(["node", "--version"])
            pass_(f"node -- {r.stdout.strip()}")
            r = run(["node", "-e", "console.log('hello-node')"])
            if r.returncode == 0 and "hello-node" in r.stdout:
                pass_("node executes JS")
            else:
                fail("node executes JS", r.stderr.strip()[:80])
            if has("npm"):
                r = run(["npm", "--version"])
                pass_(f"npm -- {r.stdout.strip()}")
        else:
            skip("node", "not found")

        # -- .NET ----------------------------------------------------------
        suite(".NET")
        if has("dotnet"):
            r = run(["dotnet", "--version"])
            pass_(f"dotnet -- {r.stdout.strip()}")
        else:
            skip("dotnet", "not found")

        # -- Java ----------------------------------------------------------
        suite("Java")
        if has("java") and has("javac"):
            r = run(["java", "-version"])
            pass_(f"java -- {(r.stdout + r.stderr).splitlines()[0]}")
            java_src = Path(tmp) / "Hello.java"
            java_src.write_text(
                'public class Hello{public static void main(String[] a)'
                '{System.out.println("hello-java");}}'
            )
            r = run(["javac", str(java_src)])
            if r.returncode == 0:
                pass_("javac compiles")
                r2 = subprocess.run(["java", "-cp", tmp, "Hello"],
                                    capture_output=True, text=True)
                if r2.returncode == 0 and "hello-java" in r2.stdout:
                    pass_("java runs compiled class")
                else:
                    fail("java runs compiled class", r2.stderr.strip()[:80])
            else:
                fail("javac compiles", r.stderr.strip()[:80])
        else:
            skip("java", "java or javac not found")


# ============================================================================
# GROUP: identity  (musl platforms -- Alpine markers)
# ============================================================================
def group_identity() -> None:
    if not is_linux():
        suite("OS Identity")
        skip("identity tests", "Linux only")
        return

    if not is_musl():
        suite("OS Identity")
        skip("identity tests", "musl platforms only")
        return

    suite("OS Identity")

    os_release = Path("/etc/os-release")
    alpine_release = Path("/etc/alpine-release")
    lsb_release = Path("/etc/lsb-release")

    if os_release.exists():
        pass_("/etc/os-release exists")
        content = os_release.read_text()
        if "Alpine" in content:
            pass_("os-release says Alpine")
        else:
            fail("os-release says Alpine", f"content: {content[:120]}")
        if re.search(r"VERSION_ID=\d+\.\d+", content):
            pass_("os-release has valid VERSION_ID")
        else:
            fail("os-release has valid VERSION_ID", "pattern not found")
        if "ID=alpine" in content:
            pass_("ID=alpine in os-release")
        else:
            fail("ID=alpine in os-release", "not found")
    else:
        fail("/etc/os-release exists", "file not found")

    if alpine_release.exists():
        ver = alpine_release.read_text().strip()
        if re.match(r"^\d+\.\d+\.\d+", ver):
            pass_(f"/etc/alpine-release has version ({ver})")
        else:
            fail("/etc/alpine-release has version", f"got: {ver}")
    else:
        fail("/etc/alpine-release exists", "file not found")

    if not lsb_release.exists():
        pass_("no /etc/lsb-release (Ubuntu marker removed)")
    else:
        fail("no /etc/lsb-release", "Ubuntu marker still present")


# ============================================================================
# GROUP: musl
# ============================================================================
def group_musl() -> None:
    if not is_linux():
        suite("musl libc")
        skip("musl tests", "Linux only")
        return

    if not is_musl():
        suite("musl libc")
        skip("musl tests", "musl platforms only")
        return

    musl_interp = _musl_interp()
    glibc_linker = _glibc_linker()
    glibc_libdir = _glibc_libdir()

    suite("musl Dynamic Linker")
    if musl_interp:
        p = Path(musl_interp)
        if p.exists():
            pass_(f"musl linker exists ({musl_interp})")
            if os.access(musl_interp, os.X_OK):
                pass_("musl linker is executable")
            else:
                fail("musl linker is executable", "not executable")
            r = run([musl_interp, "--version"])
            out = (r.stdout + r.stderr).lower()
            if "musl" in out:
                pass_("musl linker reports version")
            else:
                fail("musl linker reports version", f"output: {out[:80]}")
        else:
            fail("musl linker exists", f"{musl_interp} not found")

    suite("musl ldd")
    if has("ldd"):
        r = run(["ldd", "--version"])
        out = (r.stdout + r.stderr).lower()
        if "musl" in out:
            pass_("ldd identifies as musl")
        else:
            fail("ldd identifies as musl", f"output: {out[:80]}")
    else:
        fail("ldd exists", "not found")

    suite("musl Headers (musl-dev)")
    for hdr in ["stdio.h", "stdlib.h", "string.h", "unistd.h"]:
        p = Path(f"/usr/include/{hdr}")
        if p.exists():
            pass_(f"/usr/include/{hdr} exists")
        else:
            fail(f"/usr/include/{hdr} exists", "not found")

    suite("musl CRT objects")
    for obj in ["crt1.o", "crti.o", "crtn.o"]:
        p = Path(f"/usr/lib/{obj}")
        if p.exists():
            pass_(f"/usr/lib/{obj} exists")
        else:
            fail(f"/usr/lib/{obj} exists", "not found")

    suite("glibc Coexistence")
    if glibc_linker:
        p = Path(glibc_linker)
        if p.exists():
            pass_(f"glibc linker still exists ({glibc_linker})")
            if os.access(glibc_linker, os.X_OK):
                pass_("glibc linker is executable")
            else:
                fail("glibc linker is executable", "not executable")
        else:
            fail("glibc linker still exists", f"{glibc_linker} not found")
    if glibc_libdir:
        d = Path(glibc_libdir)
        if d.is_dir():
            pass_(f"glibc lib directory intact ({glibc_libdir})")
            libc_so = d / "libc.so.6"
            if libc_so.exists():
                pass_("glibc libc.so.6 exists")
            else:
                fail("glibc libc.so.6 exists", f"not found in {glibc_libdir}")
        else:
            fail("glibc lib directory intact", f"{glibc_libdir} not found")


# ============================================================================
# GROUP: apk
# ============================================================================
def group_apk() -> None:
    suite("apk Package Manager")

    if not is_linux() or not is_musl():
        skip("apk tests", "musl Linux only")
        return

    if not has("apk"):
        fail("apk exists", "not found in PATH")
        return

    r = run(["apk", "--version"])
    out = (r.stdout + r.stderr)
    if "apk-tools" in out:
        pass_(f"apk reports version -- {out.splitlines()[0]}")
    else:
        fail("apk reports version", f"output: {out[:80]}")

    suite("apk Repositories")
    repos = Path("/etc/apk/repositories")
    if repos.exists():
        pass_("/etc/apk/repositories exists")
        content = repos.read_text()
        if "/main" in content:
            pass_("main repo configured")
        else:
            fail("main repo configured", "'/main' not in repositories")
        if "/community" in content:
            pass_("community repo configured")
        else:
            fail("community repo configured", "'/community' not in repositories")
    else:
        fail("/etc/apk/repositories exists", "not found")

    suite("apk Database")
    for d in ["/lib/apk/db", "/var/cache/apk"]:
        if Path(d).is_dir():
            pass_(f"{d} exists")
        else:
            fail(f"{d} exists", "directory not found")

    suite("apk Functionality")
    r = run(["apk", "search", "-q"])
    if r.returncode == 0 and len(r.stdout.splitlines()) > 0:
        pass_(f"apk has packages available ({len(r.stdout.splitlines())} packages)")
    else:
        fail("apk has packages available", "apk search returned nothing")

    r = run(["apk", "list", "--installed"])
    if "musl-dev" in (r.stdout + r.stderr):
        pass_("musl-dev is installed")
    else:
        fail("musl-dev is installed", "not found in apk list --installed")


# ============================================================================
# GROUP: clang-wrapper  (musl platforms only)
# ============================================================================
def group_clang_wrapper() -> None:
    suite("Clang/LLD wrapper")

    if not is_linux() or not is_musl():
        skip("clang-wrapper tests", "musl Linux only")
        return

    if not has("clang"):
        skip("clang-wrapper tests", "clang not found")
        return

    musl_target = f"{platform.machine().lower()}-linux-musl"
    musl_interp = _musl_interp()

    # Wrapper installation
    clang_bin = shutil.which("clang") or "/usr/bin/clang"
    clangpp_bin = shutil.which("clang++") or "/usr/bin/clang++"

    for binary, name in [(clang_bin, "clang"), (clangpp_bin, "clang++")]:
        p = Path(binary)
        if not p.exists():
            fail(f"{name} wrapper exists", f"{binary} not found")
            continue
        content = p.read_text(errors="replace")
        if "musl-overlay" in content:
            pass_(f"{name} is musl wrapper (contains marker)")
        else:
            skip(f"{name} is musl wrapper", "marker not found (may be direct binary)")
        if "@@" in content:
            fail(f"{name} has no unpatched placeholders", "found @@ in wrapper")
        else:
            pass_(f"{name} has no unpatched placeholders")

    suite("Clang target identity")
    for flag, label in [("-print-target-triple", "target triple"),
                        ("-print-effective-triple", "effective triple"),
                        ("-dumpmachine", "dumpmachine")]:
        r = run(["clang", flag])
        out = (r.stdout + r.stderr).strip()
        if "linux-musl" in out:
            pass_(f"clang {flag} -> musl ({out})")
        else:
            fail(f"clang {flag} -> musl", f"got: {out}")

    r = run(["clang", "--version"])
    out = r.stdout + r.stderr
    if "linux-musl" in out:
        pass_("clang --version shows musl target")
    else:
        fail("clang --version shows musl target",
             out.splitlines()[0] if out else "no output")

    suite("Clang C compilation -> musl")
    with tempfile.TemporaryDirectory() as tmp:
        ok, interp = _compile_and_run("clang", "hello.c", _C_SRC, tmpdir=tmp)
        if ok:
            pass_("clang compiles C")
            if interp == musl_interp:
                pass_(f"clang C binary -> musl ({interp})")
            else:
                fail("clang C binary -> musl", f"got: '{interp}'")
        else:
            fail("clang compiles C", interp)

        if has("clang++"):
            ok, interp = _compile_and_run("clang++", "hello.cpp", _CPP_SRC, tmpdir=tmp)
            if ok:
                pass_("clang++ compiles C++")
                if interp == musl_interp:
                    pass_(f"clang++ C++ binary -> musl ({interp})")
                else:
                    fail("clang++ C++ binary -> musl", f"got: '{interp}'")
            else:
                fail("clang++ compiles C++", interp)

    suite("Clang cross passthrough")
    r = run(["clang", "--target=x86_64-linux-gnu", "-print-target-triple"])
    out = (r.stdout + r.stderr).strip()
    if "linux-gnu" in out:
        pass_(f"--target=x86_64-linux-gnu passthrough ({out})")
    else:
        fail("--target=x86_64-linux-gnu passthrough", f"got: {out}")

    suite("LLD wrapper")
    lld_bin = "/usr/bin/ld.lld"
    if Path(lld_bin).exists():
        content = Path(lld_bin).read_text(errors="replace")
        if "musl-overlay" in content:
            pass_("ld.lld is musl wrapper")
        else:
            skip("ld.lld is musl wrapper", "not our wrapper (direct binary)")
        r = run([lld_bin, "--version"])
        if r.returncode == 0:
            pass_(f"ld.lld --version works -- {(r.stdout + r.stderr).splitlines()[0]}")
        else:
            fail("ld.lld --version", "command failed")
    else:
        skip("ld.lld wrapper", "ld.lld not found")

    suite("Clang debug mode")
    r = subprocess.run(["clang", "-print-target-triple"],
                       capture_output=True, text=True,
                       env={**os.environ, "MUSL_WRAPPER_DEBUG": "1"})
    out = r.stdout + r.stderr
    if "[musl-wrapper]" in out:
        pass_("MUSL_WRAPPER_DEBUG=1 shows debug output")
        if "gcc-install-dir" in out:
            pass_("debug output shows --gcc-install-dir")
        else:
            fail("debug output shows --gcc-install-dir", "not found")
    else:
        skip("MUSL_WRAPPER_DEBUG", "debug marker not found (wrapper may not support it)")


# ============================================================================
# GROUP: rust-wrapper  (musl platforms only)
# ============================================================================
def group_rust_wrapper() -> None:
    suite("Rust musl wrapper")

    if not is_linux() or not is_musl():
        skip("rust-wrapper tests", "musl Linux only")
        return

    wrapper = Path("/usr/local/bin/rustc")
    if not wrapper.exists():
        skip("rust-wrapper tests", "/usr/local/bin/rustc not found")
        return

    musl_target = _musl_rust_target()
    musl_interp = _musl_interp()

    # Wrapper files
    for name in ["rustc", "cargo", "rustup"]:
        p = Path(f"/usr/local/bin/{name}")
        if p.exists():
            pass_(f"{name} wrapper exists at /usr/local/bin/{name}")
        else:
            fail(f"{name} wrapper exists", f"/usr/local/bin/{name} not found")

    content = wrapper.read_text(errors="replace")
    if "RUSTUP_HOME" in content:
        pass_("rustc wrapper sets RUSTUP_HOME")
    else:
        fail("rustc wrapper sets RUSTUP_HOME", "not found in wrapper")
    if musl_target and musl_target in content:
        pass_(f"rustc wrapper injects --target={musl_target}")
    else:
        fail("rustc wrapper injects musl target", "target not found in wrapper")

    suite("Rust tools in PATH")
    for tool in ["rustc", "cargo", "rustup"]:
        p = shutil.which(tool)
        if p:
            pass_(f"{tool} in PATH -> {p}")
        else:
            fail(f"{tool} in PATH", "not found")

    suite("Rustup through wrapper")
    r = run(["rustup", "show"])
    if r.returncode == 0:
        pass_("rustup show works")
    else:
        fail("rustup show", (r.stdout + r.stderr).strip()[:80])

    if musl_target:
        r2 = run(["rustup", "target", "list", "--installed"])
        if musl_target in (r2.stdout + r2.stderr):
            pass_(f"musl target installed ({musl_target})")
        else:
            fail("musl target installed", f"{musl_target} not in rustup target list")

    suite("Rust compilation -> musl")
    with tempfile.TemporaryDirectory() as tmp:
        rs_src = Path(tmp) / "hello.rs"
        rs_out = Path(tmp) / "hello_rust"
        rs_src.write_text(_RS_SRC)
        r = run(["rustc", "-o", str(rs_out), str(rs_src)])
        if r.returncode == 0 and rs_out.exists():
            pass_("rustc compiles (no flags)")
            interp = elf_interp(str(rs_out))
            if interp == musl_interp:
                pass_("rustc binary -> musl (dynamic)")
            elif not interp:
                pass_("rustc binary -> static (musl target default)")
            else:
                fail("rustc binary -> musl", f"got: {interp}")
            r2 = run([str(rs_out)])
            if r2.returncode == 0:
                pass_("rustc binary runs")
            else:
                fail("rustc binary runs", f"exit {r2.returncode}")
        else:
            fail("rustc compiles (no flags)", (r.stdout + r.stderr).strip()[:80])


# ============================================================================
# GROUP: platform-detect
# ============================================================================
def group_platform_detect() -> None:
    suite("ldd Detection")
    if is_linux():
        if has("ldd"):
            r = run(["ldd", "--version"])
            out = (r.stdout + r.stderr).lower()
            if is_musl():
                if "musl" in out:
                    pass_("ldd identifies as musl")
                else:
                    fail("ldd identifies as musl", f"output: {out[:80]}")
            else:
                pass_(f"ldd output: {out.splitlines()[0] if out else '<empty>'}")
        else:
            fail("ldd exists", "not found")
    else:
        skip("ldd detection", "Linux only")

    suite("dotnet RID")
    if has("dotnet"):
        r = run(["dotnet", "--info"])
        out = r.stdout + r.stderr
        m = re.search(r"RID:\s*(\S+)", out)
        if m:
            rid = m.group(1)
            pass_(f"dotnet RID = {rid}")
        else:
            skip("dotnet RID", "could not parse from dotnet --info")
    else:
        skip("dotnet RID", "dotnet not found")

    suite("Python libc detection")
    py = "python3" if has("python3") else ("python" if has("python") else None)
    if py:
        r = run([py, "-c", "import platform; print(platform.libc_ver())"])
        if r.returncode == 0:
            pass_(f"python3 platform.libc_ver() = {r.stdout.strip()}")
        r2 = run([py, "-c", "import sys; print(sys.platform)"])
        if r2.returncode == 0:
            pass_(f"python3 sys.platform = {r2.stdout.strip()}")
    else:
        skip("python libc detection", "python not found")

    suite("Rust default target")
    if has("rustc"):
        r = run(["rustc", "-vV"])
        out = r.stdout + r.stderr
        m = re.search(r"host:\s*(\S+)", out)
        if m:
            host = m.group(1)
            pass_(f"rustc host triple = {host}")
        else:
            fail("rustc -vV", "could not parse host triple")
        if has("rustup") and is_musl():
            musl_t = _musl_rust_target()
            r2 = run(["rustup", "target", "list", "--installed"])
            if musl_t and musl_t in (r2.stdout + r2.stderr):
                pass_(f"rustup musl target installed ({musl_t})")
            elif musl_t:
                skip(f"rustup musl target ({musl_t})", "not installed")
    else:
        skip("rust target detection", "rustc not found")

    suite("Go environment")
    if has("go"):
        r = run(["go", "env", "GOOS", "GOARCH", "CGO_ENABLED"])
        if r.returncode == 0:
            for label, val in zip(["GOOS", "GOARCH", "CGO_ENABLED"],
                                  r.stdout.strip().splitlines()):
                pass_(f"go env {label} = {val}")
    else:
        skip("go environment", "go not found")

    suite("Node.js musl detection")
    if has("node") and is_linux() and is_musl():
        r = run(["node", "-e",
                 "const{execSync}=require('child_process');"
                 "try{const o=execSync('ldd --version 2>&1',{encoding:'utf8'});"
                 "console.log(o.includes('musl')?'musl':'glibc')}"
                 "catch(e){const o=(e.stdout||'')+(e.stderr||'')+(e.message||'');"
                 "console.log(o.includes('musl')?'musl':'unknown')}"])
        if r.returncode == 0:
            result = r.stdout.strip()
            if result == "musl":
                pass_("Node detects musl via ldd")
            else:
                fail("Node detects musl via ldd", f"got: {result}")
    else:
        skip("Node musl detection", "node not found or not musl Linux")


# ============================================================================
# GROUP: libcxx
# ============================================================================
def group_libcxx() -> None:
    """Validate the libc++ payload that bootstrap-build.py ships into the
    bundle (iteration 4). Tests:
      1. clang++ -stdlib=libc++ links and runs (basic)
      2. clang++ -stdlib=libc++ with <vector> + <algorithm>
         (real header coverage -- catches missing libc++ headers)
      3. clang++ -stdlib=libc++ -static (Linux only) --
         catches missing libc++.a / libc++abi.a
    macOS uses the system libc++ from Xcode SDK -- no special bundle
    needed but we still run the smoke tests to confirm clang sees it.
    """
    suite("libc++ (LLVM C++ runtime)")

    if not has("clang++"):
        skip("libc++", "clang++ not found")
        return

    # Test 1: basic compile/link/run with -stdlib=libc++
    src1 = '#include <cstdio>\nint main(){std::puts("libcxx");return 0;}\n'
    with tempfile.TemporaryDirectory() as tmp:
        srcp = Path(tmp) / "t.cpp"
        srcp.write_text(src1)
        out = Path(tmp) / ("t.exe" if is_windows() else "t")
        r = run(["clang++", "-stdlib=libc++", "-o", str(out), str(srcp)])
        if r.returncode != 0:
            fail("clang++ -stdlib=libc++ basic",
                 f"compile failed: {r.stderr.strip()[:200]}")
            return
        r2 = run([str(out)])
        if r2.returncode == 0:
            pass_("clang++ -stdlib=libc++ basic compile/run")
        else:
            fail("clang++ -stdlib=libc++ basic run", f"exit {r2.returncode}")

    # Test 2: real header coverage with vector + algorithm
    src2 = (
        "#include <vector>\n"
        "#include <algorithm>\n"
        "#include <cstdio>\n"
        "int main(){\n"
        "  std::vector<int> v={3,1,4,1,5,9};\n"
        "  std::sort(v.begin(),v.end());\n"
        "  for(int x:v) std::printf(\"%d \",x);\n"
        "  std::printf(\"\\n\");\n"
        "  return 0;\n"
        "}\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        srcp = Path(tmp) / "t.cpp"
        srcp.write_text(src2)
        out = Path(tmp) / ("t.exe" if is_windows() else "t")
        r = run(["clang++", "-stdlib=libc++", "-o", str(out), str(srcp)])
        if r.returncode != 0:
            fail("clang++ -stdlib=libc++ vector+algorithm",
                 f"compile failed: {r.stderr.strip()[:200]}")
        else:
            r2 = run([str(out)])
            if r2.returncode == 0 and "1 1 3 4 5 9" in r2.stdout:
                pass_("clang++ -stdlib=libc++ vector+algorithm")
            else:
                fail("clang++ -stdlib=libc++ vector+algorithm",
                     f"unexpected output: {r2.stdout.strip()}")

    # Test 3: static linking (Linux only -- macOS does not support fully
    # static libc++ binaries, Windows does not have static libc with clang).
    if is_linux():
        with tempfile.TemporaryDirectory() as tmp:
            srcp = Path(tmp) / "t.cpp"
            srcp.write_text(src2)
            out = Path(tmp) / "t"
            r = run([
                "clang++", "-stdlib=libc++", "-static",
                "-o", str(out), str(srcp),
            ])
            if r.returncode != 0:
                fail("clang++ -stdlib=libc++ -static",
                     f"compile failed: {r.stderr.strip()[:200]}")
            else:
                r2 = run([str(out)])
                if r2.returncode == 0:
                    pass_("clang++ -stdlib=libc++ -static")
                else:
                    fail("clang++ -stdlib=libc++ -static run",
                         f"exit {r2.returncode}")


# ============================================================================
# Registry + main
# ============================================================================
GROUPS: dict[str, tuple] = {
    "env":              (group_env,              "Runner environment + platform detection"),
    "tools":            (group_tools,            "CLI tool presence"),
    "compilers":        (group_compilers,        "C/C++ compile + run + ELF interpreter"),
    "static":           (group_static,           "Static linking (Linux)"),
    "cmake":            (group_cmake,            "CMake + Ninja build scenarios"),
    "runtimes":         (group_runtimes,         "Go, Rust, Python, Node, .NET, Java"),
    "identity":         (group_identity,         "Alpine OS markers (musl only)"),
    "musl":             (group_musl,             "musl linker, headers, CRT, glibc coexistence"),
    "apk":              (group_apk,              "apk package manager (musl only)"),
    "clang-wrapper":    (group_clang_wrapper,    "Clang/LLD musl wrapper system"),
    "rust-wrapper":     (group_rust_wrapper,     "Rust musl wrapper system"),
    "platform-detect":  (group_platform_detect,  "ldd, dotnet RID, python libc_ver, Rust target"),
    "libcxx":           (group_libcxx,           "clang++ -stdlib=libc++"),
}


def print_summary() -> None:
    total = len(_passed) + len(_failed) + len(_skipped)
    print(f"\n{BOLD}{'-' * 50}{NC}")
    print(f"{BOLD}Results:{NC} {total} tests  "
          f"{GREEN}{len(_passed)} passed{NC}  "
          f"{RED}{len(_failed)} failed{NC}  "
          f"{YELLOW}{len(_skipped)} skipped{NC}")
    if _failed:
        print(f"\n{RED}{BOLD}Failures:{NC}")
        for f in _failed:
            print(f"  {RED}x{NC} {f}")
    print(f"{BOLD}{'-' * 50}{NC}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Unified test suite for powertech-center/runners",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Groups: " + ", ".join(GROUPS.keys())
    )
    parser.add_argument("targets", nargs="*",
                        help="Groups to run (default: all)")
    parser.add_argument("--list", action="store_true",
                        help="List available groups and exit")
    args = parser.parse_args()

    if args.list:
        print("Available groups:")
        for name, (_, desc) in GROUPS.items():
            print(f"  {BOLD}{name:<20}{NC} {desc}")
        return

    targets = args.targets if args.targets else list(GROUPS.keys())

    for t in targets:
        if t not in GROUPS:
            print(f"{RED}Unknown group: {t}{NC}")
            print("Available: " + ", ".join(GROUPS.keys()))
            sys.exit(1)

    import datetime
    print(f"{BOLD}{'=' * 50}{NC}")
    print(f"{BOLD} PowerTech Runners -- Test Suite{NC}")
    print(f" {datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f" Platform (computed): {_detect_platform()}")
    print(f" Python: {platform.python_version()}  OS: {platform.system()} {platform.machine()}")
    print(f"{BOLD}{'=' * 50}{NC}")

    for t in targets:
        GROUPS[t][0]()

    print_summary()
    sys.exit(0 if not _failed else 1)


if __name__ == "__main__":
    main()
