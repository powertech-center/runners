#!/usr/bin/env python3
"""
bootstrap-build.py — formation of bootstrap ROOT directory for one of the
8 target platforms supported by powertech-center/runners.

Output: a populated ROOT directory containing files laid out with
absolute filesystem paths from "/" (or "C:\\"). The directory is
ready to be packed into a single patch.tar.gz blob and published as an
OCI artifact to ghcr.io/powertech-center/runners/{platform}:latest.

Usage:
    python3 bootstrap-build.py <platform> [--root <dir>] [--pack]

If --root is not provided, defaults to:
    $RUNNER_TEMP/bootstrap-root           (on CI runners)
    <repo>/.tmp/bootstrap-root            (for local exploration)

If --pack is given, also creates patch.tar.gz next to the root directory.

Supported platforms (full set, but only the listed ones are implemented
in the current iteration -- others will raise NotImplementedError):

    linux-x64-gnu       not yet implemented
    linux-x64-musl      not yet implemented
    linux-arm64-gnu     not yet implemented
    linux-arm64-musl    not yet implemented
    macos-x64           not yet implemented
    macos-arm64         not yet implemented
    windows-x64         IMPLEMENTED
    windows-arm64       not yet implemented

See CLAUDE.md section "bootstrap-build.py -- статус и план разработки"
for the full plan and design rationale.
"""

import argparse
import os
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PLATFORMS = (
    "linux-x64-gnu",
    "linux-x64-musl",
    "linux-arm64-gnu",
    "linux-arm64-musl",
    "macos-x64",
    "macos-arm64",
    "windows-x64",
    "windows-arm64",
)

REPO_ROOT = Path(__file__).resolve().parent

# Subdirectory inside ROOT where Windows installs Chocolatey-managed tools.
# This is the standard location used by all Windows GitHub-hosted runners.
WIN_CHOCO_BIN_REL = Path("ProgramData") / "Chocolatey" / "bin"

# llvm-mingw: self-contained MinGW-targeting LLVM toolchain from
# https://github.com/mstorsjo/llvm-mingw — used on windows-arm64 where
# the stock LLVM targets aarch64-pc-windows-msvc (not MinGW) and there
# is no native GCC. Includes clang, lld, gcc/g++ shims, libc++, and
# the MinGW-w64 sysroot.
LLVM_MINGW_RELEASE = "20260407"
LLVM_MINGW_LLVM_VERSION = "22.1.3"

# Where LLVM lives on Windows runners.
WIN_LLVM_DIR = Path("Program Files") / "LLVM"
WIN_LLVM_BACKUP = Path("Program Files") / "_LLVM"


# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------

def info(msg: str) -> None:
    print(f"[bootstrap-build] {msg}", flush=True)


def group(title: str) -> None:
    # GitHub Actions log group, no-op outside CI.
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::group::{title}", flush=True)
    else:
        print(f"=== {title} ===", flush=True)


def endgroup() -> None:
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print("::endgroup::", flush=True)


def fail(msg: str) -> "NoReturn":
    print(f"[bootstrap-build] ERROR: {msg}", flush=True)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Common helpers
# ---------------------------------------------------------------------------

def run(cmd, cwd=None, check=True, env=None):
    """Run a command, streaming output. Accepts a list or a string."""
    if isinstance(cmd, str):
        printable = cmd
        shell = True
    else:
        printable = " ".join(str(c) for c in cmd)
        shell = False
    info(f"$ {printable}")
    result = subprocess.run(cmd, cwd=cwd, env=env, shell=shell)
    if check and result.returncode != 0:
        fail(f"command failed with exit code {result.returncode}: {printable}")
    return result.returncode


def run_capture(cmd, cwd=None, check=True, env=None) -> str:
    """Run a command and capture stdout. Return stdout string."""
    if isinstance(cmd, str):
        printable = cmd
        shell = True
    else:
        printable = " ".join(str(c) for c in cmd)
        shell = False
    info(f"$ {printable}")
    result = subprocess.run(cmd, cwd=cwd, env=env, shell=shell, capture_output=True, text=True)
    if check and result.returncode != 0:
        fail(f"command failed with exit code {result.returncode}: {printable}\n{result.stderr}")
    return result.stdout


def download(url: str, dest: Path) -> None:
    """Download a URL to dest with a few retries."""
    info(f"download: {url}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    last_err = None
    for attempt in range(1, 6):
        try:
            with urllib.request.urlopen(url, timeout=60) as resp, open(dest, "wb") as out:
                shutil.copyfileobj(resp, out)
            return
        except Exception as e:  # noqa: BLE001 -- want broad retry
            last_err = e
            info(f"  attempt {attempt} failed: {e}")
    fail(f"failed to download {url}: {last_err}")


def find_tool(name: str) -> str | None:
    """Return absolute path to an executable on PATH, or None."""
    return shutil.which(name)


def ensure_clean_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def pack_tar_gz(root: Path, archive: Path) -> None:
    """Pack the contents of root/ into archive (tar.gz). Paths inside the
    archive are relative to root, so distribution layout matches what
    `tar -xzf archive -C /` (or C:\\) expects.

    If ROOT contains nothing, a single zero-byte marker file
    `.bootstrap-empty` is added so that downstream tools (oras push,
    GHCR) get a non-empty blob and run/action.yml has something
    deterministic to extract.
    """
    archive.parent.mkdir(parents=True, exist_ok=True)

    files = [e for e in root.rglob("*") if e.is_file() or e.is_symlink()]
    if not files:
        marker = root / ".bootstrap-empty"
        marker.write_bytes(b"")
        info("ROOT was empty; added .bootstrap-empty marker")

    info(f"pack: {root} -> {archive}")
    with tarfile.open(archive, "w:gz", compresslevel=9) as tf:
        for entry in sorted(root.rglob("*")):
            arcname = entry.relative_to(root).as_posix()
            tf.add(entry, arcname=arcname, recursive=False)
    size_mb = archive.stat().st_size / (1024 * 1024)
    info(f"archive size: {size_mb:.2f} MB")


# ---------------------------------------------------------------------------
# Soft host cleanup
# ---------------------------------------------------------------------------
#
# Packages that this script installs on the build runner as a side-effect
# of populating ROOT (apt on Linux, brew on macOS, choco on Windows) are
# tracked here and uninstalled at the end of the build, BEFORE the
# workflow emulates the consumer bootstrap. The goal: after cleanup, the
# runner should look (approximately) like a fresh one -- so when the
# emulate step extracts patch.tar.gz into / and runs bootstrap-test.py,
# the tests really exercise files from our bundle, not leftovers from
# apt/brew/choco.
#
# "Soft" cleanup: we only remove what WE installed in this run. Anything
# that was already on the runner stays untouched, and transitive brew
# dependencies also stay (safer than a deep purge). It's a best-effort
# extra layer of confidence, not a hermetic rebuild.
#
# Linux musl does not use host package managers at all -- its Alpine
# overlay lives entirely inside ROOT -- so cleanup is a no-op there.

_host_installed: list[tuple[str, str]] = []  # (manager, package)


def _apt_list_installed(packages: list[str]) -> set[str]:
    """Return the subset of `packages` that dpkg reports as installed."""
    if not packages:
        return set()
    result = subprocess.run(
        ["dpkg-query", "-W", "-f=${Package} ${Status}\n", *packages],
        capture_output=True, text=True,
    )
    installed: set[str] = set()
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[-1] == "installed":
            installed.add(parts[0])
    return installed


def host_install_apt(packages: list[str]) -> None:
    """Install apt packages, tracking only the ones we actually added.

    Packages already installed on the runner are left out of the tracker
    so that cleanup never removes pre-existing host state.
    """
    if not packages:
        return
    before = _apt_list_installed(packages)
    new_pkgs = [p for p in packages if p not in before]
    if not new_pkgs:
        info(f"apt: all requested packages already installed: {' '.join(packages)}")
        return
    info(f"apt: installing {' '.join(new_pkgs)}")
    run("sudo apt-get update -qq")
    run(["sudo", "apt-get", "install", "-y", "--no-install-recommends", *new_pkgs])
    for p in new_pkgs:
        _host_installed.append(("apt", p))


def _brew_list_installed() -> set[str]:
    result = subprocess.run(
        ["brew", "list", "--formula"], capture_output=True, text=True,
    )
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def host_install_brew(packages: list[str]) -> None:
    """brew install the given formulae, tracking only the ones we added."""
    if not packages:
        return
    before = _brew_list_installed()
    for pkg in packages:
        if pkg in before:
            info(f"brew: {pkg} already installed")
            continue
        info(f"brew: installing {pkg}")
        run(["brew", "install", pkg])
        _host_installed.append(("brew", pkg))


def _choco_list_installed() -> set[str]:
    result = subprocess.run(
        ["choco", "list", "--local-only", "--limit-output"],
        capture_output=True, text=True,
    )
    names: set[str] = set()
    for line in result.stdout.splitlines():
        name = line.split("|", 1)[0].strip().lower()
        if name:
            names.add(name)
    return names


def host_install_choco(packages: list[str]) -> None:
    """choco install the given packages, tracking only the ones we added."""
    if not packages:
        return
    before = _choco_list_installed()
    for pkg in packages:
        if pkg.lower() in before:
            info(f"choco: {pkg} already installed")
            continue
        info(f"choco: installing {pkg}")
        run(["choco", "install", "-y", "--no-progress", pkg])
        _host_installed.append(("choco", pkg))


def host_cleanup_installed() -> None:
    """Uninstall everything we added via host_install_* in this run.

    Best-effort: errors are logged but do not fail the build. The
    emulate+test steps in the workflow are what ultimately validate the
    bundle -- cleanup just makes those steps more meaningful by removing
    our own transient host state first.
    """
    if not _host_installed:
        info("host cleanup: nothing to remove")
        return

    by_mgr: dict[str, list[str]] = {}
    for mgr, pkg in _host_installed:
        by_mgr.setdefault(mgr, []).append(pkg)

    group("Soft host cleanup (remove packages we installed)")
    if "apt" in by_mgr:
        # Only remove the explicit packages we asked apt to install.
        # Deliberately NOT running `apt-get autoremove` -- it would also
        # purge transitive dependencies that happened to become orphaned,
        # including pre-existing host packages that apt considers auto-
        # installed (seen in CI: autoremove took out liblldb-16t64 /
        # liblldb-17t64 which were part of the base runner image).
        # That violates the "soft" contract. Runtime libs installed as
        # deps (e.g. libc++1-18) stay on the host, which is fine -- the
        # gating test validates files from the bundle, and we ship both
        # static and dynamic libc++ runtimes into ROOT.
        info(f"apt remove --purge: {' '.join(by_mgr['apt'])}")
        run(["sudo", "apt-get", "remove", "--purge", "-y", *by_mgr["apt"]], check=False)
    if "brew" in by_mgr:
        for pkg in by_mgr["brew"]:
            info(f"brew uninstall: {pkg}")
            run(["brew", "uninstall", "--ignore-dependencies", pkg], check=False)
    if "choco" in by_mgr:
        for pkg in by_mgr["choco"]:
            info(f"choco uninstall: {pkg}")
            run(["choco", "uninstall", "-y", pkg], check=False)
    _host_installed.clear()
    endgroup()


# ---------------------------------------------------------------------------
# ROOT layout helpers
# ---------------------------------------------------------------------------

def default_root() -> Path:
    runner_temp = os.environ.get("RUNNER_TEMP")
    if runner_temp:
        return Path(runner_temp) / "bootstrap-root"
    return REPO_ROOT / ".tmp" / "bootstrap-root"


def prepare_root(root: Path) -> None:
    info(f"ROOT: {root}")
    ensure_clean_dir(root)


# ---------------------------------------------------------------------------
# libc++ subsystem
# ---------------------------------------------------------------------------
#
# clang ships with C, libc, and libc++ headers via its resource directory
# (`clang -print-resource-dir`), but on most stock distributions the
# headers and static archives for the C++ standard library (`libc++`,
# `libc++abi`, `libunwind`) are NOT included by default. Without them,
# `clang++ -stdlib=libc++` fails to find <vector>, <string>, etc.
#
# We ship a static libc++ for every platform that has clang but lacks
# libc++ in the host runner image: Linux (gnu and musl) and Windows.
# macOS already provides libc++ via the Xcode SDK and is excluded.
#
# Layout per platform (paths are relative to ROOT, i.e. relative to
# "/" or "C:\\" after extraction):
#
#   linux-x64-gnu     usr/include/c++/v1/                 (headers)
#                     usr/lib/x86_64-linux-gnu/libc++*.a  (static archives)
#   linux-arm64-gnu   usr/include/c++/v1/
#                     usr/lib/aarch64-linux-gnu/libc++*.a
#   linux-x64-musl    usr/include/c++/v1/
#                     usr/lib/libc++*.a
#   linux-arm64-musl  usr/include/c++/v1/
#                     usr/lib/libc++*.a
#   windows-x64       Program Files/LLVM/include/c++/v1/
#                     Program Files/LLVM/lib/libc++*.a
#   windows-arm64     same as windows-x64
#
# Strategy per platform:
#   - Linux gnu: install via `apt-get install libc++-dev libc++abi-dev`
#     and copy the installed headers and static archives out of /usr
#     into ROOT. This is fast and uses the distro's tested binaries.
#     We deliberately skip libunwind-dev: ubuntu-24.04 runners already
#     ship libunwind-18-dev (from LLVM 18) which declares `Conflicts:
#     libunwind-dev`, and apt-installed libc++abi links against the
#     system libgcc_s unwinder so libunwind is not required at runtime.
#   - Linux musl: deferred to iteration 5 (depends on the apk overlay).
#   - Windows: build from llvm-project sources (~minutes) because no
#     prepackaged static libc++ for Windows exists. Install into ROOT
#     under Program Files/LLVM.
#
# Detailed CMake reference: C:\PowerTech\Dockers\cross-clang\Dockerfile
# .template Stage 3 (lines ~331-397). The runtimes build uses cmake
# modules from llvm-project/runtimes directly.

LIBCXX_LLVM_VERSION_DEFAULT = "19.1.7"


def _libcxx_linux_gnu(arch: str, root: Path) -> None:
    """Install libc++ static libs and headers via apt and copy into ROOT.

    apt installs into /usr/include/c++/v1/ (headers) and
    /usr/lib/<triplet>/ (libraries). We copy what we need into ROOT
    preserving the absolute paths so that `tar -xzf -C /` lays it down
    where clang will find it via -stdlib=libc++.

    Dynamically detects the major version of the installed clang (e.g. 18, 19)
    and installs matching libc++-{version}-dev / libc++abi-{version}-dev /
    libunwind-{version}-dev to avoid version mismatches and package conflicts.
    """
    triplet = "x86_64-linux-gnu" if arch == "x64" else "aarch64-linux-gnu"

    # Detect clang version dynamically
    clang_version_output = run_capture("clang --version", check=False).strip()
    # Parse "... version X.Y.Z ..." (e.g. "Ubuntu clang version 18.1.3")
    import re
    match = re.search(r"version\s+(\d+)\.", clang_version_output)
    if not match:
        fail(f"could not detect clang major version from: {clang_version_output}")
    llvm_major = match.group(1)
    info(f"detected LLVM major version: {llvm_major}")

    # Install both -dev packages (headers + static archives + .so symlinks)
    # and the runtime packages (libc++1-N / libc++abi1-N / libunwind-N)
    # that provide the actual .so.1 shared objects. We list the runtime
    # packages explicitly so that host_cleanup_installed() can remove
    # them by name without running `apt-get autoremove` (which would also
    # purge unrelated orphaned packages and violate the soft-cleanup
    # contract). If any of these are already present on the runner,
    # host_install_apt diffs them out and they will not be tracked.
    group(f"apt-get install libc++-{llvm_major}-dev libc++abi-{llvm_major}-dev libunwind-{llvm_major}-dev")
    host_install_apt([
        f"libc++1-{llvm_major}",
        f"libc++abi1-{llvm_major}",
        f"libunwind-{llvm_major}",
        f"libc++-{llvm_major}-dev",
        f"libc++abi-{llvm_major}-dev",
        f"libunwind-{llvm_major}-dev",
    ])
    endgroup()

    # 1. Headers: copy /usr/include/c++/v1 verbatim.
    src_headers = Path("/usr/include/c++/v1")
    if not src_headers.is_dir():
        fail(f"libc++ headers not found at {src_headers} after apt install")
    dst_headers = root / "usr" / "include" / "c++" / "v1"
    info(f"copy headers: {src_headers} -> {dst_headers}")
    _copytree_preserve(src_headers, dst_headers)

    # 2. Libraries. On ubuntu-24.04, libc++-18-dev / libunwind-18-dev
    #    lay the REAL files under /usr/lib/llvm-18/lib/ (libc++.a,
    #    libc++.so.1.0, libunwind.a, libunwind.so.1.0, ...) and install
    #    only forwarding symlinks like libc++.so -> ../llvm-18/lib/libc++.so
    #    in /usr/lib/<triplet>/. We therefore copy from BOTH locations:
    #    real files from /usr/lib/llvm-<major>/lib/, and the indirection
    #    symlinks from the triplet dir. _copy_link_or_file preserves
    #    the relative symlink targets via os.readlink so once both
    #    directories are in ROOT the links resolve correctly.
    #
    #    WARNING: the triplet also contains an unrelated system libunwind
    #    (from libunwind-dev, API 0.99, libunwind.so.8*, libunwind.a not
    #    forwarded) which is NOT what clang -lunwind wants. We therefore
    #    copy only the specific forwarding symlinks we need from the
    #    triplet, not broad libunwind* globs.
    src_triplet = Path("/usr/lib") / triplet
    if not src_triplet.is_dir():
        src_triplet = Path("/usr/lib")
    src_llvm = Path(f"/usr/lib/llvm-{llvm_major}/lib")

    dst_triplet = root / "usr" / "lib" / triplet
    dst_triplet.mkdir(parents=True, exist_ok=True)
    dst_llvm = root / "usr" / "lib" / f"llvm-{llvm_major}" / "lib"
    dst_llvm.mkdir(parents=True, exist_ok=True)

    copied = 0
    # Real libc++ / libc++abi / LLVM libunwind files live in
    # /usr/lib/llvm-<major>/lib/ on noble.
    if src_llvm.is_dir():
        llvm_patterns = (
            "libc++*.a", "libc++abi*.a",
            "libc++*.so*", "libc++abi*.so*",
            "libunwind*.a", "libunwind*.so*",
        )
        for pattern in llvm_patterns:
            for src in src_llvm.glob(pattern):
                dst = dst_llvm / src.name
                info(f"copy lib: {src} -> {dst}")
                _copy_link_or_file(src, dst)
                copied += 1
    else:
        info(f"note: {src_llvm} does not exist; libc++ must live directly in {src_triplet}")

    # Triplet dir: copy ONLY the forwarding symlinks that point at
    # llvm-<major>/lib/. Using a broad libunwind*.so* glob would drag in
    # the system libunwind (libunwind.so.8*) which is incompatible with
    # LLVM libunwind and breaks clang -stdlib=libc++ -lunwind linking.
    triplet_names = (
        "libc++.a", "libc++abi.a", "libc++experimental.a",
        "libc++.so", "libc++.so.1", "libc++.so.1.0",
        "libc++abi.so", "libc++abi.so.1", "libc++abi.so.1.0",
        "libunwind.a",
        "libunwind.so", "libunwind.so.1", "libunwind.so.1.0",
    )
    for name in triplet_names:
        src = src_triplet / name
        if src.exists() or src.is_symlink():
            dst = dst_triplet / name
            info(f"copy lib: {src} -> {dst}")
            _copy_link_or_file(src, dst)
            copied += 1

    if copied == 0:
        fail(f"no libc++ libraries found in {src_triplet} or {src_llvm}")
    info(f"copied {copied} libc++ libraries")


def _libcxx_windows(arch: str, root: Path) -> None:
    """Build libc++ from llvm-project sources and install into ROOT under
    Program Files/LLVM.

    Stock LLVM Windows installers do not include libc++ headers or
    static archives. Building from source is the only reliable way to
    get a static libc++ usable with `clang++ -stdlib=libc++` on Windows.
    Reference: cross-clang Stage 3, simplified for native (non-cross)
    build on the runner host.
    """
    cmake = find_tool("cmake")
    ninja = find_tool("ninja")
    clang = find_tool("clang")
    clangpp = find_tool("clang++")
    if not all((cmake, ninja, clang, clangpp)):
        fail("Windows libc++ build requires cmake, ninja, clang, clang++ on PATH")

    work = root.parent / f"libcxx-build-{arch}"
    ensure_clean_dir(work)

    src_archive = work / "llvm.tar.xz"
    version = os.environ.get("LIBCXX_LLVM_VERSION", LIBCXX_LLVM_VERSION_DEFAULT)
    url = (
        f"https://github.com/llvm/llvm-project/releases/download/"
        f"llvmorg-{version}/llvm-project-{version}.src.tar.xz"
    )
    download(url, src_archive)

    group("Extract llvm-project sources")
    # Only extract the directories needed by the runtimes build.
    needed = [
        f"llvm-project-{version}.src/runtimes",
        f"llvm-project-{version}.src/libcxx",
        f"llvm-project-{version}.src/libcxxabi",
        f"llvm-project-{version}.src/libunwind",
        f"llvm-project-{version}.src/cmake",
        f"llvm-project-{version}.src/llvm/cmake",
        f"llvm-project-{version}.src/llvm/utils",
        f"llvm-project-{version}.src/third-party",
    ]
    import tarfile as _tarfile
    with _tarfile.open(src_archive) as tf:
        members = []
        for m in tf.getmembers():
            if any(m.name.startswith(prefix) for prefix in needed):
                members.append(m)
        tf.extractall(work, members=members)
    src_root = work / f"llvm-project-{version}.src"
    if not (src_root / "runtimes").is_dir():
        fail(f"runtimes directory not found in extracted source: {src_root}")
    endgroup()

    install_prefix_abs = root / "Program Files" / "LLVM"

    # Target triple: GNU-like mode, not MSVC/clang-cl.  clang on Windows
    # defaults to x86_64-pc-windows-msvc which pulls in vcruntime headers
    # and conflicts with libc++ type_info.  We need the MinGW/GNU ABI so
    # that `clang++ -stdlib=libc++` works without clang-cl.
    if arch == "x64":
        target_triple = "x86_64-pc-windows-gnu"
    else:
        target_triple = "aarch64-pc-windows-gnu"

    build = work / "build"
    group("CMake configure libc++")
    run([
        cmake, "-G", "Ninja",
        "-S", str(src_root / "runtimes"),
        "-B", str(build),
        f"-DCMAKE_C_COMPILER={clang}",
        f"-DCMAKE_CXX_COMPILER={clangpp}",
        f"-DCMAKE_C_COMPILER_TARGET={target_triple}",
        f"-DCMAKE_CXX_COMPILER_TARGET={target_triple}",
        f"-DLLVM_DEFAULT_TARGET_TRIPLE={target_triple}",
        # Avoid link-testing the compiler — windows-arm64 runners lack a
        # MinGW sysroot so the linker cannot find -lkernel32 etc.  We only
        # build static libraries, so a link test is unnecessary.
        "-DCMAKE_TRY_COMPILE_TARGET_TYPE=STATIC_LIBRARY",
        "-DCMAKE_BUILD_TYPE=Release",
        "-DLLVM_ENABLE_RUNTIMES=libcxx;libcxxabi;libunwind",
        "-DLIBCXX_ENABLE_SHARED=OFF",
        "-DLIBCXX_ENABLE_STATIC=ON",
        "-DLIBCXXABI_ENABLE_SHARED=OFF",
        "-DLIBCXXABI_ENABLE_STATIC=ON",
        "-DLIBUNWIND_ENABLE_SHARED=OFF",
        "-DLIBUNWIND_ENABLE_STATIC=ON",
        "-DLIBCXXABI_USE_LLVM_UNWINDER=ON",
        "-DLIBCXX_INCLUDE_BENCHMARKS=OFF",
        "-DLIBCXX_INCLUDE_TESTS=OFF",
        "-DLIBCXXABI_INCLUDE_TESTS=OFF",
        "-DLIBUNWIND_INCLUDE_TESTS=OFF",
        f"-DCMAKE_INSTALL_PREFIX={install_prefix_abs}",
    ])
    endgroup()

    group("Build libc++")
    run([ninja, "-C", str(build)])
    endgroup()

    group("Install libc++ into ROOT")
    run([ninja, "-C", str(build), "install"])
    endgroup()


def install_libcxx(platform: str, root: Path) -> None:
    """Dispatch libc++ install for the given platform. macOS is a no-op."""
    if platform.startswith("macos"):
        info("libc++: skipped on macOS (provided by Xcode SDK)")
        return
    if platform == "linux-x64-gnu":
        _libcxx_linux_gnu("x64", root)
    elif platform == "linux-arm64-gnu":
        _libcxx_linux_gnu("arm64", root)
    elif platform == "linux-x64-musl":
        # Handled inside the musl overlay (apk add libc++-dev) in
        # iteration 5.
        info("libc++: linux musl uses apk install (handled by overlay)")
    elif platform == "linux-arm64-musl":
        info("libc++: linux musl uses apk install (handled by overlay)")
    elif platform == "windows-arm64":
        info("libc++: skipped on windows-arm64 (included in llvm-mingw)")
    elif platform == "windows-x64":
        _libcxx_windows("x64", root)
    else:
        fail(f"install_libcxx: unsupported platform {platform}")


# ---------------------------------------------------------------------------
# Platform: windows-x64
# ---------------------------------------------------------------------------
#
# On the GitHub-hosted windows-latest runner most utilities are already
# present (curl, jq, tar, 7z, ...). The bootstrap bundle needs only the
# tools that are MISSING from a fresh runner so that the rest of the
# runners workflow surface (artifact upload/download, scripts, etc.)
# can rely on them. Each tool is detected on the host first; if it is
# already there, we skip it.
#
# Two categories of tools:
#
# Standalone (downloaded as individual binaries into ProgramData\Chocolatey\bin):
#   - wget   (prebuilt EXE from eternallybored.org)
#   - yq     (prebuilt EXE from mikefarah/yq releases)
#   - zstd   (built from source with Visual Studio 2022)
#   - ld.lld (copy of lld.exe — GNU-compatible LLD frontend for MinGW)
#
# MSYS2 (installed via pacman, files collected by snapshot diff into msys64/):
#   - zip, rsync, tree, pkg-config (pkgconf)
#   Uses the same before/after diff approach as brew on macOS.


def _win_choco_bin(root: Path) -> Path:
    target = root / WIN_CHOCO_BIN_REL
    target.mkdir(parents=True, exist_ok=True)
    return target


def _win_add_wget(arch: str, bin_dir: Path) -> None:
    wget_arch = "a64" if arch == "arm64" else "64"
    url = f"https://eternallybored.org/misc/wget/1.21.4/{wget_arch}/wget.exe"
    download(url, bin_dir / "wget.exe")


def _win_add_yq(arch: str, bin_dir: Path) -> None:
    yq_arch = "arm64" if arch == "arm64" else "amd64"
    url = f"https://github.com/mikefarah/yq/releases/latest/download/yq_windows_{yq_arch}.exe"
    download(url, bin_dir / "yq.exe")


def _win_add_zstd(arch: str, bin_dir: Path, work_root: Path) -> None:
    cmake = find_tool("cmake")
    if cmake is None:
        fail("cmake not found on PATH; required to build zstd")

    cmake_arch = "ARM64" if arch == "arm64" else "x64"
    work = work_root / "zstd"
    ensure_clean_dir(work)

    archive = work / "zstd.zip"
    download("https://github.com/facebook/zstd/archive/refs/tags/v1.5.7.zip", archive)

    # Use the standard library to unzip; avoids relying on tar/Expand-Archive.
    import zipfile
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(work)

    src_dirs = [p for p in work.iterdir() if p.is_dir() and p.name.startswith("zstd-")]
    if not src_dirs:
        fail("unable to locate extracted zstd sources")
    src = src_dirs[0]

    build = work / "build"
    run([
        cmake,
        "-S", str(src / "build" / "cmake"),
        "-B", str(build),
        "-G", "Visual Studio 17 2022",
        "-A", cmake_arch,
        "-Wno-dev",
        "-DCMAKE_BUILD_TYPE=Release",
        "-DZSTD_BUILD_PROGRAMS=ON",
        "-DZSTD_BUILD_SHARED=OFF",
    ])
    run([cmake, "--build", str(build), "--config", "Release", "--target", "zstd"])

    candidates = list(build.rglob("zstd.exe"))
    if not candidates:
        fail("zstd.exe not found after build")
    shutil.copy2(candidates[0], bin_dir / "zstd.exe")


MSYS2_ROOT = Path(r"C:\msys64")

# Map of tools that come from MSYS2 packages.
# key = tool name (as in WIN_TOOLS), value = MSYS2 package name.
WIN_MSYS2_TOOLS: dict[str, str] = {
    "zip":        "zip",
    "rsync":      "rsync",
    "tree":       "tree",
    "pkg-config": "pkgconf",
}


def _win_msys2_pacman() -> str:
    """Return path to pacman.exe inside the MSYS2 installation on the runner."""
    p = MSYS2_ROOT / "usr" / "bin" / "pacman.exe"
    if p.exists():
        return str(p)
    fail("pacman not found in C:\\msys64 — MSYS2 must be pre-installed on the runner")


def _win_pacman_snapshot() -> set[str]:
    """Return the set of currently installed MSYS2 packages."""
    out = run_capture([_win_msys2_pacman(), "-Qq"])
    return {line.strip() for line in out.splitlines() if line.strip()}


def _win_install_msys2_tools(missing_tools: list[str], root: Path) -> None:
    """Install missing MSYS2 packages via pacman and collect new files into ROOT.

    Uses the brew-style snapshot approach: snapshot installed packages
    before, install what's needed, diff, then copy all new files into ROOT
    preserving the msys64/ tree so that tar -xzf -C C:\\ lands them in
    C:\\msys64\\... where they belong (and where msys-2.0.dll lives).

    Executables from usr/bin/ are also placed into
    ROOT/ProgramData/Chocolatey/bin/ so they appear on PATH without
    requiring C:\\msys64\\usr\\bin in PATH (not guaranteed on all runners).
    """
    pacman = _win_msys2_pacman()
    pkgs = [WIN_MSYS2_TOOLS[t] for t in missing_tools]

    before = _win_pacman_snapshot()
    info(f"MSYS2 packages before install: {len(before)}")

    info(f"syncing MSYS2 package database")
    run([pacman, "-Sy", "--noconfirm"])

    info(f"installing MSYS2 packages: {' '.join(pkgs)}")
    run([pacman, "-S", "--noconfirm", "--needed"] + pkgs)

    after = _win_pacman_snapshot()
    new_pkgs = sorted(after - before)
    info(f"new MSYS2 packages (with deps): {len(new_pkgs)}")
    for pkg in new_pkgs:
        info(f"  + {pkg}")

    if not new_pkgs:
        info("no new MSYS2 packages installed (all were already present)")
        return

    choco_bin = root / WIN_CHOCO_BIN_REL
    choco_bin.mkdir(parents=True, exist_ok=True)

    # Collect files from each new package into ROOT/msys64/...
    for pkg in new_pkgs:
        out = run_capture([pacman, "-Ql", pkg])
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            # Format: "pkgname /usr/bin/foo.exe"
            parts = line.split(None, 1)
            if len(parts) < 2:
                continue
            msys_path = parts[1]  # e.g. /usr/bin/rsync.exe
            # Skip directories (end with /)
            if msys_path.endswith("/"):
                continue
            # Convert MSYS2 path to Windows path under C:\msys64
            src = MSYS2_ROOT / msys_path.lstrip("/").replace("/", "\\")
            if not src.exists():
                continue
            # Place in ROOT as msys64/... so tar -xzf -C C:\ works
            rel = Path("msys64") / msys_path.lstrip("/")
            dst = root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

            # Also put executables into ProgramData/Chocolatey/bin/ for PATH
            if msys_path.startswith("/usr/bin/") and src.suffix.lower() in (".exe", ".dll"):
                choco_dst = choco_bin / src.name
                if not choco_dst.exists():
                    shutil.copy2(src, choco_dst)

    files = sum(1 for _ in (root / "msys64").rglob("*") if _.is_file())
    info(f"MSYS2 files in ROOT: {files}")


def _win_add_ld_lld(bin_dir: Path) -> None:
    """Copy lld.exe as ld.lld.exe — GNU-compatible LLD frontend for MinGW."""
    lld = find_tool("lld") or find_tool("lld.exe")
    if lld is None:
        fail("lld.exe not found on PATH; cannot create ld.lld copy")
    shutil.copy2(lld, bin_dir / "ld.lld.exe")


def _win_install_llvm_mingw(root: Path) -> None:
    """Download llvm-mingw (native ARM64) and place it into ROOT so that
    after extraction it lands at C:\\Program Files\\LLVM.

    On consumer runners run/action.yml renames the stock MSVC-targeting
    LLVM to _LLVM before extracting the bundle, so our files take over
    the canonical C:\\Program Files\\LLVM path and land on PATH.

    llvm-mingw includes: clang/clang++ targeting aarch64-w64-mingw32,
    gcc/g++ wrapper shims, lld, llvm-ar, libc++ (static + shared),
    libunwind, and the full MinGW-w64 sysroot (headers + import libs).
    """
    work = root.parent / "llvm-mingw-work"
    ensure_clean_dir(work)

    archive = work / "llvm-mingw.zip"
    url = (
        f"https://github.com/mstorsjo/llvm-mingw/releases/download/"
        f"{LLVM_MINGW_RELEASE}/llvm-mingw-{LLVM_MINGW_RELEASE}-ucrt-aarch64.zip"
    )
    download(url, archive)

    group("Extract llvm-mingw")
    import zipfile
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(work)

    # The zip contains a single top-level dir like
    # llvm-mingw-20260407-ucrt-aarch64/
    extracted = [p for p in work.iterdir()
                 if p.is_dir() and p.name.startswith("llvm-mingw-")]
    if not extracted:
        fail("llvm-mingw: could not find extracted directory")
    src = extracted[0]
    info(f"llvm-mingw extracted: {src.name}")
    endgroup()

    # Place into ROOT so tar -xzf -C C:\ puts it at C:\Program Files\LLVM
    dst = root / WIN_LLVM_DIR
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)

    # llvm-mingw ships ld.lld.exe but may omit bare lld.exe which tests
    # expect.  Create a copy so both names resolve on PATH.
    bin_dst = dst / "bin"
    lld_exe = bin_dst / "lld.exe"
    ld_lld_exe = bin_dst / "ld.lld.exe"
    if not lld_exe.exists() and ld_lld_exe.exists():
        shutil.copy2(ld_lld_exe, lld_exe)
        info("created lld.exe as copy of ld.lld.exe")

    n_files = sum(1 for _ in dst.rglob("*") if _.is_file())
    info(f"llvm-mingw installed into ROOT/{WIN_LLVM_DIR}: {n_files} files")


# MSYS2 sfx for windows-arm64: there is no pre-installed MSYS2 on
# windows-11-arm runners and no native arm64 builds of zip/rsync.  We
# download the x86_64 MSYS2 sfx, install zip + rsync via pacman, then
# copy the minimal set of .exe + msys-*.dll into
# ROOT/ProgramData/Chocolatey/bin/.  The binaries run under x64
# emulation (xtajit64.dll) on arm64.  Diagnostic run 24397871149
# confirmed the exact dependency graph captured below.
WIN_ARM64_MSYS2_SFX_URL = (
    "https://github.com/msys2/msys2-installer/releases/download/"
    "nightly-x86_64/msys2-base-x86_64-latest.sfx.exe"
)

# Files to lift out of MSYS2 /usr/bin into the bundle.  Keyed by tool
# name; each entry lists the .exe plus the msys-*.dll it depends on
# (per `ldd` output from the diagnostic probe).  Duplicates between
# tools are deduped when copying.
WIN_ARM64_MSYS2_FILES: dict[str, list[str]] = {
    "zip": [
        "zip.exe",
        "msys-2.0.dll",
    ],
    "rsync": [
        "rsync.exe",
        "msys-2.0.dll",
        "msys-crypto-3.dll",
        "msys-iconv-2.dll",
        "msys-zstd-1.dll",
        "msys-lz4-1.dll",
        "msys-xxhash-0.dll",
    ],
}


def _win_arm64_install_msys2_tools(root: Path, work_root: Path) -> None:
    """Install zip + rsync from MSYS2 (x86_64) into the windows-arm64 bundle.

    MSYS2 is not pre-installed on windows-11-arm runners and has no
    native arm64 build for these tools.  We download the sfx, run
    pacman, then lift the minimal set of .exe + msys-*.dll into
    ROOT/ProgramData/Chocolatey/bin/ so they appear on PATH as-is
    after the bundle is extracted on a consumer.
    """
    msys_root = work_root / "msys64"
    if msys_root.exists():
        shutil.rmtree(msys_root)

    sfx = work_root / "msys2.sfx.exe"
    download(WIN_ARM64_MSYS2_SFX_URL, sfx)

    group("Extract MSYS2 sfx")
    # The sfx extracts msys64/ into its current working directory.
    # Invoked with no args to match the diagnostic probe (run 24397871149).
    run([str(sfx)], cwd=work_root)
    bash = msys_root / "usr" / "bin" / "bash.exe"
    if not bash.exists():
        fail(f"MSYS2 bash not found at {bash} after sfx extraction")
    endgroup()

    group("Install zip + rsync via pacman")
    # --login sources /etc/profile so /usr/bin ends up on PATH; without
    # it pacman-key/pacman are not found.  Fresh sfx already has base
    # keys populated, so we skip `pacman-key --init` and only sync the
    # repo database.
    run([str(bash), "--login", "-c", "pacman -Syy --noconfirm"])
    run([str(bash), "--login", "-c",
         "pacman -S --noconfirm --needed zip rsync"])
    endgroup()

    src_bin = msys_root / "usr" / "bin"
    choco_bin = root / WIN_CHOCO_BIN_REL
    choco_bin.mkdir(parents=True, exist_ok=True)

    wanted: set[str] = set()
    for files in WIN_ARM64_MSYS2_FILES.values():
        wanted.update(files)

    for name in sorted(wanted):
        src = src_bin / name
        if not src.exists():
            fail(f"expected MSYS2 file missing after pacman: {src}")
        shutil.copy2(src, choco_bin / name)
        info(f"  + {name} ({src.stat().st_size:,} bytes)")


def build_windows(arch: str, root: Path) -> None:
    """Build a Windows bootstrap ROOT for the given arch (x64 or arm64).

    Standalone tools (wget, yq, zstd, ld.lld) are downloaded as individual
    binaries into ROOT/ProgramData/Chocolatey/bin/.

    MSYS2 tools (zip, rsync, tree, pkg-config) are installed via pacman and
    their files are collected into ROOT/msys64/... using a before/after
    snapshot diff (same approach as brew on macOS). On consumer runners
    C:\\msys64 is already on PATH, so the binaries work out of the box.
    """
    if sys.platform != "win32":
        fail(f"build_windows must run on a Windows host (sys.platform={sys.platform})")

    bin_dir = _win_choco_bin(root)
    work_root = root.parent / f"bootstrap-work-{arch}"
    ensure_clean_dir(work_root)

    # -- Standalone tools (individual downloads) ----------------------------
    standalone_handlers = {
        "wget":   lambda: _win_add_wget(arch, bin_dir),
        "yq":     lambda: _win_add_yq(arch, bin_dir),
        "zstd":   lambda: _win_add_zstd(arch, bin_dir, work_root),
        "ld.lld": lambda: _win_add_ld_lld(bin_dir),
    }

    for tool, handler in standalone_handlers.items():
        group(f"Tool: {tool}")
        existing = find_tool(tool) or find_tool(f"{tool}.exe")
        if existing:
            info(f"{tool}: present on runner at {existing}, skipping")
        else:
            info(f"{tool}: missing on runner, adding to bundle")
            handler()
        endgroup()

    # -- MSYS2 tools (via pacman snapshot diff) -----------------------------
    msys2_missing = []
    for tool in WIN_MSYS2_TOOLS:
        existing = find_tool(tool) or find_tool(f"{tool}.exe")
        if existing:
            info(f"{tool}: present on runner at {existing}, skipping")
        else:
            info(f"{tool}: missing on runner, will install via MSYS2")
            msys2_missing.append(tool)

    if msys2_missing:
        group(f"MSYS2 install: {' '.join(msys2_missing)}")
        _win_install_msys2_tools(msys2_missing, root)
        endgroup()

    # libc++ for clang -- not present on stock Windows runners.
    group("libc++ install")
    install_libcxx(f"windows-{arch}", root)
    endgroup()

    files = list(root.rglob("*"))
    info(f"ROOT contains {sum(1 for f in files if f.is_file())} files")


def build_windows_x64(root: Path) -> None:
    build_windows("x64", root)


def build_windows_arm64(root: Path) -> None:
    """Build Windows ARM64 bootstrap ROOT.

    Key difference from x64: the stock LLVM on the runner targets
    aarch64-pc-windows-msvc and there is no native GCC or MSYS2.
    We replace the entire LLVM installation with llvm-mingw, which
    provides a MinGW-targeting toolchain (clang, gcc/g++ shims, lld,
    libc++, and a full MinGW-w64 sysroot).  libc++ is included so
    install_libcxx() is a no-op for this platform.

    MSYS2 tools: windows-11-arm runners have no pre-installed MSYS2
    and no native arm64 build of zip/rsync exists.  We download the
    MSYS2 x86_64 sfx, install zip + rsync via pacman, and copy the
    minimal set of .exe + msys-*.dll into
    ROOT/ProgramData/Chocolatey/bin/.  These run under x64 emulation
    (xtajit64.dll) on arm64.  tree/pkg-config are not bundled for
    arm64 yet — add them here if bootstrap-test.py grows tests that
    need them.
    """
    if sys.platform != "win32":
        fail(f"build_windows_arm64 must run on a Windows host (sys.platform={sys.platform})")

    bin_dir = _win_choco_bin(root)
    work_root = root.parent / "bootstrap-work-arm64"
    ensure_clean_dir(work_root)

    # -- llvm-mingw: replaces stock MSVC-targeting LLVM ----------------------
    group("llvm-mingw toolchain")
    _win_install_llvm_mingw(root)
    endgroup()

    # -- Standalone tools (same as x64 minus ld.lld which comes with
    #    llvm-mingw) -----------------------------------------------------------
    standalone_handlers = {
        "wget": lambda: _win_add_wget("arm64", bin_dir),
        "yq":   lambda: _win_add_yq("arm64", bin_dir),
        "zstd": lambda: _win_add_zstd("arm64", bin_dir, work_root),
    }

    for tool, handler in standalone_handlers.items():
        group(f"Tool: {tool}")
        existing = find_tool(tool) or find_tool(f"{tool}.exe")
        if existing:
            info(f"{tool}: present on runner at {existing}, skipping")
        else:
            info(f"{tool}: missing on runner, adding to bundle")
            handler()
        endgroup()

    # -- MSYS2 tools: zip + rsync via x86_64 sfx + emulation -----------------
    group("MSYS2 tools (zip, rsync) via x86_64 sfx")
    _win_arm64_install_msys2_tools(root, work_root)
    endgroup()

    files = list(root.rglob("*"))
    info(f"ROOT contains {sum(1 for f in files if f.is_file())} files")


# ---------------------------------------------------------------------------
# Platform: macos-x64 / macos-arm64
# ---------------------------------------------------------------------------
#
# macOS GitHub-hosted runners come with Homebrew preinstalled. The
# bootstrap bundle ships any brew formulae that are NOT present on a
# fresh runner so that downstream jobs can rely on them.
#
# Procedure:
#   1. Detect which tools from MACOS_TOOLS[<platform>] are missing.
#   2. Snapshot `brew list --formula` before installing.
#   3. `brew install` the missing tools.
#   4. Diff snapshots to find every formula that landed (includes
#      transitive dependencies).
#   5. For each new formula, copy:
#         - $(brew --cellar)/<pkg>            -> ROOT/<cellar-rel>/<pkg>
#         - any prefix symlinks pointing into Cellar/<pkg>/
#         - $(brew --prefix)/opt/<pkg>        -> ROOT/<prefix-rel>/opt/<pkg>
#      preserving the absolute filesystem layout from "/" so that
#      `tar -xzf patch.tar.gz -C /` lands files exactly where brew
#      expects them.
#
# Critical difference from the legacy bootstrap-build-publish.yml: the legacy
# code packed everything under $BUNDLE/Cellar/... without a prefix,
# which is incompatible with the unified extract-to-root bootstrap
# model. This Python rewrite fixes that by computing the absolute
# brew prefix and laying out files under ROOT/<that prefix without
# leading slash>/.

MACOS_TOOLS = {
    "macos-x64": ("tree",),
    "macos-arm64": ("tree", "php", "composer"),
}


def _macos_brew_paths() -> tuple[Path, Path]:
    """Return (brew_prefix, brew_cellar) as absolute Paths.

    On Apple Silicon brew_prefix is /opt/homebrew; on Intel it is
    /usr/local. We trust `brew --prefix` / `brew --cellar` rather than
    hardcoding either, so the script remains correct on any future
    runner image.
    """
    brew = find_tool("brew")
    if brew is None:
        fail("brew not found on PATH; required on macOS runners")
    prefix = Path(subprocess.check_output([brew, "--prefix"], text=True).strip())
    cellar = Path(subprocess.check_output([brew, "--cellar"], text=True).strip())
    if not prefix.is_absolute() or not cellar.is_absolute():
        fail(f"brew returned non-absolute paths: prefix={prefix} cellar={cellar}")
    return prefix, cellar


def _abs_to_rel_under_root(absolute: Path) -> Path:
    """Convert /opt/homebrew/foo -> opt/homebrew/foo for use under ROOT."""
    parts = absolute.parts
    if parts and parts[0] == "/":
        parts = parts[1:]
    elif absolute.is_absolute():
        # POSIX absolute path; strip the leading "/" component.
        parts = absolute.parts[1:]
    return Path(*parts)


def _copytree_preserve(src: Path, dst: Path) -> None:
    """Recursively copy src into dst, preserving symlinks and metadata."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, symlinks=True)


def _copy_link_or_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() or dst.is_symlink():
        dst.unlink()
    if src.is_symlink():
        link_target = os.readlink(src)
        os.symlink(link_target, dst)
    elif src.is_dir():
        _copytree_preserve(src, dst)
    else:
        shutil.copy2(src, dst)


def _macos_brew_snapshot() -> set[str]:
    out = subprocess.check_output(["brew", "list", "--formula"], text=True)
    return {line.strip() for line in out.splitlines() if line.strip()}


def build_macos(platform: str, root: Path) -> None:
    if sys.platform != "darwin":
        fail(f"build_macos must run on a macOS host (sys.platform={sys.platform})")

    tools = MACOS_TOOLS[platform]
    info(f"target tools: {' '.join(tools)}")

    missing = [t for t in tools if find_tool(t) is None]
    if not missing:
        info("all target tools already present on runner; bundle will be empty")
        return

    info(f"missing tools (will brew install): {' '.join(missing)}")

    before = _macos_brew_snapshot()
    info(f"brew packages before install: {len(before)}")

    group(f"brew install {' '.join(missing)}")
    host_install_brew(missing)
    endgroup()

    after = _macos_brew_snapshot()
    new_pkgs = sorted(after - before)
    info(f"new brew packages (with transitive deps): {len(new_pkgs)}")
    for pkg in new_pkgs:
        info(f"  + {pkg}")

    if not new_pkgs:
        fail("no new packages detected after brew install -- bundle would be empty")

    prefix, cellar = _macos_brew_paths()
    info(f"brew prefix: {prefix}")
    info(f"brew cellar: {cellar}")

    cellar_rel = _abs_to_rel_under_root(cellar)
    prefix_rel = _abs_to_rel_under_root(prefix)

    # 1. Copy each new package's Cellar directory into ROOT preserving
    #    the absolute layout.
    for pkg in new_pkgs:
        src = cellar / pkg
        if not src.exists():
            info(f"  skip {pkg}: {src} does not exist")
            continue
        dst = root / cellar_rel / pkg
        info(f"  copy Cellar/{pkg}")
        _copytree_preserve(src, dst)

    # 2. Copy prefix symlinks (bin/, lib/, etc.) that point into the
    #    new packages' Cellar trees. brew creates these on `brew install`
    #    via `brew link`, and they are what makes installed binaries
    #    appear on PATH.
    for subdir in ("bin", "sbin", "lib", "include", "share", "etc"):
        src_dir = prefix / subdir
        if not src_dir.is_dir():
            continue
        for pkg in new_pkgs:
            needle = f"Cellar/{pkg}/"
            for entry in src_dir.iterdir():
                if not entry.is_symlink():
                    continue
                try:
                    target = os.readlink(entry)
                except OSError:
                    continue
                if needle in target:
                    rel = _abs_to_rel_under_root(entry)
                    dst = root / rel
                    _copy_link_or_file(entry, dst)

    # 3. Copy the prefix/opt/<pkg> symlinks (these are how brew exposes
    #    `brew --prefix <pkg>`).
    for pkg in new_pkgs:
        opt_link = prefix / "opt" / pkg
        if opt_link.exists() or opt_link.is_symlink():
            rel = _abs_to_rel_under_root(opt_link)
            dst = root / rel
            _copy_link_or_file(opt_link, dst)

    files = sum(1 for _ in root.rglob("*"))
    info(f"ROOT contains {files} entries")


def build_macos_x64(root: Path) -> None:
    build_macos("macos-x64", root)


def build_macos_arm64(root: Path) -> None:
    build_macos("macos-arm64", root)


# ---------------------------------------------------------------------------
# Platform: linux-x64-gnu / linux-arm64-gnu
# ---------------------------------------------------------------------------
#
# The ubuntu-24.04 (and ubuntu-24.04-arm) GitHub-hosted runners ship a
# very rich toolchain by default: gcc, g++, clang, cmake, ninja, make,
# python3, node/npm, go, rustc, cargo, dotnet, java, ruby, perl, php,
# jq, yq, curl, wget, git, git-lfs, zip/unzip, zstd, xz, p7zip, tree,
# and more. For the "gnu" platforms in iteration 3 there is therefore
# nothing to ship that is not already on the runner.
#
# Iteration 4 will add libc++ (built natively from llvm-project sources)
# under /usr/include/c++/v1 and /usr/lib/{x86_64,aarch64}-linux-gnu, so
# the bundle becomes non-empty later. For now we audit a small set of
# expected tools and produce an empty ROOT, which the publish step
# packs into an empty (gzip-only) tar.gz. An empty bundle is a legal
# outcome -- run/action.yml extracts it as a no-op.

LINUX_GNU_EXPECTED = (
    "gcc", "g++", "clang", "cmake", "ninja", "make", "python3", "node",
    "go", "rustc", "cargo", "dotnet", "java", "git", "jq", "curl",
)


def build_linux_gnu(arch: str, root: Path) -> None:
    if sys.platform != "linux":
        fail(f"build_linux_gnu must run on Linux (sys.platform={sys.platform})")

    info(f"arch: {arch}")
    group("Audit expected host tools")
    missing = []
    for tool in LINUX_GNU_EXPECTED:
        path = find_tool(tool)
        if path is None:
            missing.append(tool)
            info(f"  MISSING {tool}")
        else:
            info(f"  OK      {tool} -> {path}")
    endgroup()

    if missing:
        info(f"WARNING: {len(missing)} expected tools missing on runner: {' '.join(missing)}")

    group("libc++ install")
    install_libcxx(f"linux-{arch}-gnu", root)
    endgroup()

    group("LLVM binutils (lld, llvm-ar) shims")
    _install_llvm_binutils_linux_gnu(root)
    endgroup()

    files = sum(1 for _ in root.rglob("*"))
    info(f"ROOT contains {files} entries")


def _install_llvm_binutils_linux_gnu(root: Path) -> None:
    """Expose `lld`, `ld.lld`, `llvm-ar` under unversioned names.

    ubuntu-24.04 ships `lld-<major>`, `ld.lld-<major>`, `llvm-ar-<major>`
    from the preinstalled `lld-<major>` / `llvm-<major>` apt packages on
    all GitHub-hosted ubuntu-24.04 runners (both x64 and arm64). These
    binaries and their backing libLLVM*.so* already live on the host,
    so we do NOT copy them into the bundle -- that would drag ~40 MB
    of LLVM core libraries (libLLVM-18.so.*) into an otherwise tiny
    libc++-only bundle.

    Instead we ship only three absolute-target symlinks in ROOT:

        ROOT/usr/bin/lld      -> /usr/bin/lld-<major>
        ROOT/usr/bin/ld.lld   -> /usr/bin/ld.lld-<major>
        ROOT/usr/bin/llvm-ar  -> /usr/bin/llvm-ar-<major>

    After `tar -xzf -C /` on the consumer runner, bare `lld` / `ld.lld`
    / `llvm-ar` resolve via PATH to the host-installed versioned
    binaries. This couples us to ubuntu-24.04 shipping `lld-<major>` /
    `llvm-<major>` preinstalled -- not a new dependency: we already
    assume ubuntu-24.04 + clang-<major> + libc++-<major>-dev via
    _libcxx_linux_gnu(), so one more preinstalled LLVM package is not
    a meaningful additional risk.

    Install-on-consumer (apt-get inside run/action.yml) was rejected
    because it burns ~10s on every consumer invocation for tooling
    that only a handful of tests reference.
    """
    clang_out = run_capture("clang --version", check=False).strip()
    import re
    m = re.search(r"version\s+(\d+)\.", clang_out)
    if not m:
        fail(f"could not detect clang major version from: {clang_out}")
    llvm_major = m.group(1)
    info(f"LLVM major: {llvm_major}")

    # Sanity check: the versioned binaries we symlink to must exist on
    # the build runner right now. If this fails the ubuntu-24.04 image
    # has drifted and we need to revisit the strategy.
    for name in ("lld", "ld.lld", "llvm-ar"):
        versioned = Path(f"/usr/bin/{name}-{llvm_major}")
        if not versioned.exists():
            fail(
                f"expected preinstalled {versioned} on ubuntu-24.04 runner; "
                f"image may have drifted -- re-evaluate _install_llvm_binutils_linux_gnu"
            )
        info(f"host has {versioned}")

    dst_usr_bin = root / "usr" / "bin"
    dst_usr_bin.mkdir(parents=True, exist_ok=True)
    for name in ("lld", "ld.lld", "llvm-ar"):
        link = dst_usr_bin / name
        target = f"/usr/bin/{name}-{llvm_major}"
        if link.exists() or link.is_symlink():
            link.unlink()
        link.symlink_to(target)
        info(f"symlink: {link} -> {target}")


def build_linux_x64_gnu(root: Path) -> None:
    build_linux_gnu("x64", root)


def build_linux_arm64_gnu(root: Path) -> None:
    build_linux_gnu("arm64", root)


# ---------------------------------------------------------------------------
# Platform: linux-x64-musl / linux-arm64-musl  (Alpine overlay)
# ---------------------------------------------------------------------------
#
# Strategy: build a self-contained Alpine rootfs INSIDE ROOT (not on the
# live filesystem) so that the resulting tar.gz, when extracted at "/"
# on a consumer's ubuntu-24.04 runner, turns it into a working
# Alpine-like environment for musl builds. Sources of logic:
#
#   - C:\PowerTech\pseudo-alpine\init.sh   (444 lines, lives-on-host)
#   - C:\PowerTech\pseudo-alpine\scripts\find-alpine-version.py
#
# init.sh does its work by modifying the LIVE host filesystem (cp into
# /lib/, /usr/sbin/, mv /usr/include/<triplet> -> .disabled, apk add
# gcc on the host). For bootstrap-build.py we cannot do that: we must
# produce a self-contained ROOT directory that captures all the files
# the consumer needs.
#
# Approach: download the Alpine minirootfs tarball, extract it directly
# into ROOT (so ROOT/lib/ld-musl-*.so.1, ROOT/usr/sbin/apk, etc. all
# land naturally), then:
#   1. Place a /usr/bin/ldd musl shim into ROOT/usr/bin/ldd
#   2. Place /etc/alpine-release and /etc/os-release in ROOT
#   3. Run `apk --root <ROOT> --initdb add ...` to install gcc/g++/
#      musl-dev/libstdc++-dev/libc++-dev/libc++-static/libc++abi-static
#      INTO the ROOT directory (apk supports --root for offline image
#      builds, no chroot needed).
#   4. Hide glibc multiarch headers by writing the .disabled marker
#      INTO ROOT (the consumer's bootstrap step will see both versions
#      and pick up the musl one through the standard search path).
#
# Notes / known limitations of this iteration:
#   - The clang/rust wrapper system from pseudo-alpine is NOT yet
#     ported. Wrappers are needed for `clang -> musl by default` and
#     `rustc -> --target=musl`. They are non-critical for basic gcc
#     builds and will land in a follow-up if needed.
#   - find-alpine-version.py (dynamic Alpine branch selection by host
#     GCC major version) is not yet ported. We use latest-stable for
#     the first pass; if header incompatibilities surface in CI we
#     will port the dynamic selector.

ALPINE_MIRROR = "https://dl-cdn.alpinelinux.org/alpine"
ALPINE_BRANCH_DEFAULT = "latest-stable"


def _alpine_arch_name(arch: str) -> str:
    return "x86_64" if arch == "x64" else "aarch64"


def _alpine_minirootfs_url(arch: str, branch: str) -> tuple[str, str]:
    """Return (tarball_url, version) for the latest Alpine minirootfs of
    the given branch and architecture."""
    alpine_arch = _alpine_arch_name(arch)
    index_url = f"{ALPINE_MIRROR}/{branch}/releases/{alpine_arch}/latest-releases.yaml"
    info(f"fetching Alpine release index: {index_url}")
    with urllib.request.urlopen(index_url, timeout=60) as resp:
        text = resp.read().decode("utf-8", errors="replace")

    # The YAML index lists multiple flavors; pick the alpine-minirootfs
    # entry without bringing in a YAML dep. Each entry is a YAML block
    # introduced by `flavor:` and containing a `file:` line.
    in_block = False
    minirootfs_file = None
    for line in text.splitlines():
        stripped = line.strip()
        if "flavor:" in stripped and "alpine-minirootfs" in stripped:
            in_block = True
            continue
        if in_block and stripped.startswith("file:"):
            value = stripped.split(":", 1)[1].strip()
            if value.startswith("alpine-minirootfs-") and value.endswith(".tar.gz"):
                minirootfs_file = value
                break
        if in_block and stripped.startswith("- "):
            in_block = False
    if not minirootfs_file:
        fail(f"could not find alpine-minirootfs in {index_url}")

    # Strip prefix/suffix to recover version: "alpine-minirootfs-3.21.0-x86_64.tar.gz"
    prefix = "alpine-minirootfs-"
    suffix = f"-{alpine_arch}.tar.gz"
    version = minirootfs_file[len(prefix):-len(suffix)]
    url = f"{ALPINE_MIRROR}/{branch}/releases/{alpine_arch}/{minirootfs_file}"
    return url, version


def _extract_tar_into(archive: Path, dest: Path) -> None:
    """Extract a tarball preserving permissions and symlinks. Used for
    Alpine minirootfs which contains absolute symlinks; tarfile handles
    them correctly when the destination is a fresh empty directory."""
    dest.mkdir(parents=True, exist_ok=True)
    info(f"extract {archive} -> {dest}")
    with tarfile.open(archive, "r:gz") as tf:
        tf.extractall(dest)


def _write_ldd_shim(root: Path, alpine_arch: str) -> None:
    ldd = root / "usr" / "bin" / "ldd"
    ldd.parent.mkdir(parents=True, exist_ok=True)
    ldd.write_text(
        "#!/bin/sh\n"
        f"exec /lib/ld-musl-{alpine_arch}.so.1 --list \"$@\"\n"
    )
    ldd.chmod(0o755)
    info("wrote ldd musl shim")


def _ensure_apk_repositories(root: Path, branch: str) -> None:
    """Write /etc/apk/repositories inside ROOT to point at the chosen
    Alpine branch. The minirootfs sometimes leaves this file empty."""
    repo_file = root / "etc" / "apk" / "repositories"
    repo_file.parent.mkdir(parents=True, exist_ok=True)
    repo_file.write_text(
        f"{ALPINE_MIRROR}/{branch}/main\n"
        f"{ALPINE_MIRROR}/{branch}/community\n"
    )
    info(f"wrote /etc/apk/repositories ({branch})")


# Packages we want available inside the bundle. The list mirrors what
# init.sh installs (musl-dev gcc g++ libstdc++-dev) plus the libc++
# trio that satisfies our libc++ contract for musl platforms.
MUSL_APK_PACKAGES = (
    "musl-dev",
    "gcc",
    "g++",
    "libstdc++-dev",
    "libc++-dev",
    "libc++-static",
    "libc++abi-static",
)


def _apk_install_into_root(root: Path) -> None:
    """Run apk with --root pointed at our ROOT to install packages.
    Prefers the static apk binary that came with the freshly extracted
    Alpine minirootfs (ROOT/sbin/apk) so we do not need apk-tools on
    the host at all -- ubuntu-24.04 noble dropped it from universe.
    """
    candidate = root / "sbin" / "apk.static"
    if not candidate.exists():
        candidate = root / "sbin" / "apk"
    apk: str | None = None
    if candidate.exists() and os.access(candidate, os.X_OK):
        apk = str(candidate)
    else:
        apk = find_tool("apk") or find_tool("apk.static")
    if apk is None:
        fail(
            "apk not found: neither ROOT/sbin/apk from the extracted "
            "minirootfs nor a host apk binary is available"
        )

    info(f"using apk: {apk}")
    cmd = [
        apk,
        "--root", str(root),
        "--initdb",
        "--no-scripts",
        "--allow-untrusted",
        "add",
        *MUSL_APK_PACKAGES,
    ]
    run(cmd)


def build_linux_musl(arch: str, root: Path) -> None:
    if sys.platform != "linux":
        fail(f"build_linux_musl must run on Linux (sys.platform={sys.platform})")

    alpine_arch = _alpine_arch_name(arch)
    branch = os.environ.get("ALPINE_BRANCH", ALPINE_BRANCH_DEFAULT)
    info(f"alpine_arch={alpine_arch} branch={branch}")

    # Step 1: download minirootfs
    group("Download Alpine minirootfs")
    url, version = _alpine_minirootfs_url(arch, branch)
    info(f"alpine version: {version}")
    work = root.parent / f"alpine-work-{arch}"
    ensure_clean_dir(work)
    archive = work / "minirootfs.tar.gz"
    download(url, archive)
    endgroup()

    # Step 2: extract directly into ROOT. The tarball already lays out
    # /etc, /usr, /lib, etc., so this single step seeds the entire
    # Alpine identity layer.
    group("Extract minirootfs into ROOT")
    _extract_tar_into(archive, root)
    endgroup()

    # Step 3: write Alpine identity files
    group("Identity files")
    (root / "etc" / "alpine-release").write_text(version + "\n")
    info("wrote /etc/alpine-release")
    # /etc/os-release came from the minirootfs already; trust it.
    endgroup()

    # Step 4: ldd shim
    group("ldd musl shim")
    _write_ldd_shim(root, alpine_arch)
    endgroup()

    # Step 5: apk repositories
    group("apk repositories")
    _ensure_apk_repositories(root, branch)
    endgroup()

    # Step 6: install Alpine packages into ROOT via apk --root
    group("apk add (into ROOT)")
    _apk_install_into_root(root)
    endgroup()

    files = sum(1 for _ in root.rglob("*"))
    info(f"ROOT contains {files} entries")


def build_linux_x64_musl(root: Path) -> None:
    build_linux_musl("x64", root)


def build_linux_arm64_musl(root: Path) -> None:
    build_linux_musl("arm64", root)


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

def not_implemented(name: str):
    def _impl(_root: Path) -> None:
        fail(f"platform '{name}' is not yet implemented in bootstrap-build.py")
    return _impl


BUILDERS = {
    "linux-x64-gnu": build_linux_x64_gnu,
    "linux-x64-musl": build_linux_x64_musl,
    "linux-arm64-gnu": build_linux_arm64_gnu,
    "linux-arm64-musl": build_linux_arm64_musl,
    "macos-x64": build_macos_x64,
    "macos-arm64": build_macos_arm64,
    "windows-x64": build_windows_x64,
    "windows-arm64": build_windows_arm64,
}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Build a bootstrap ROOT for one platform.")
    parser.add_argument("platform", choices=PLATFORMS, help="Target platform triplet")
    parser.add_argument("--root", type=Path, default=None, help="Output ROOT directory (defaults to $RUNNER_TEMP/bootstrap-root or ./.tmp/bootstrap-root)")
    parser.add_argument("--pack", action="store_true", help="Also pack ROOT into patch.tar.gz next to it")
    args = parser.parse_args()

    root = args.root or default_root()
    prepare_root(root)

    builder = BUILDERS[args.platform]
    info(f"platform: {args.platform}")
    builder(root)

    # Soft host cleanup: remove packages we added to the runner during
    # build, so the subsequent emulate+test steps validate the bundle
    # instead of leftover host state. Musl bypasses host package
    # managers entirely, so there is nothing to clean up there.
    if not args.platform.endswith("-musl"):
        host_cleanup_installed()

    if args.pack:
        archive = root.parent / "patch.tar.gz"
        pack_tar_gz(root, archive)

    info("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
