$failed = 0

function Test-Tool {
    param([string]$cmd, [string[]]$cmdArgs = @("--version"))
    if (Get-Command $cmd -ErrorAction SilentlyContinue) {
        try {
            $output = & $cmd @cmdArgs 2>&1 | Select-Object -First 1
            Write-Host "OK   $cmd — $output"
        } catch {
            Write-Host "OK   $cmd"
        }
    } else {
        Write-Host "FAIL $cmd — not found"
        $script:failed = 1
    }
}

# ── Runner environment ────────────────────────────────────────────────────────
Write-Host "=== Runner environment ==="
foreach ($name in @("RUNNER_PLATFORM", "RUNNER_OS", "RUNNER_ARCH", "RUNNER_NAME")) {
    $value = [Environment]::GetEnvironmentVariable($name)
    Write-Host "$name`: $(if ($value) { $value } else { '<not set>' })"
}

# ── Core utilities ────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "=== Core utilities ==="
Test-Tool "bash"     @("--version")
Test-Tool "pwsh"     @("--version")
Test-Tool "git"      @("--version")
Test-Tool "git-lfs"  @("version")
Test-Tool "curl"     @("--version")
Test-Tool "wget"     @("--version")
Test-Tool "aria2c"   @("--version")
Test-Tool "tar"      @("--version")
Test-Tool "xz"       @("--version")
Test-Tool "zstd"     @("--version")
Test-Tool "zip"      @("-v")
Test-Tool "unzip"    @("-v")
Test-Tool "7z"       @("i")
Test-Tool "rsync"    @("--version")
Test-Tool "gpg"      @("--version")
Test-Tool "gh"       @("--version")
Test-Tool "aws"      @("--version")
Test-Tool "az"       @("--version")

# ── Text processing ───────────────────────────────────────────────────────────
Write-Host ""
Write-Host "=== Text processing ==="
Test-Tool "jq"       @("--version")
Test-Tool "yq"       @("--version")
Test-Tool "grep"     @("--version")
Test-Tool "sed"      @("--version")
Test-Tool "awk"      @("--version")
Test-Tool "find"     @("--version")
Test-Tool "tree"     @("--version")

# ── Build systems ─────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "=== Build systems ==="
Test-Tool "cmake"       @("--version")
Test-Tool "ninja"       @("--version")
Test-Tool "make"        @("--version")
Test-Tool "pkg-config"  @("--version")
Test-Tool "gradle"      @("--version")
Test-Tool "mvn"         @("--version")
Test-Tool "ant"         @("-version")
Test-Tool "bazel"       @("version")
Test-Tool "bazelisk"    @("version")

# ── GCC toolchain ─────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "=== GCC toolchain ==="
Test-Tool "gcc"      @("--version")
Test-Tool "g++"      @("--version")
Test-Tool "ld"       @("--version")
Test-Tool "ar"       @("--version")
Test-Tool "nm"       @("--version")
Test-Tool "objdump"  @("--version")

# ── Clang/LLVM toolchain ──────────────────────────────────────────────────────
Write-Host ""
Write-Host "=== Clang/LLVM toolchain ==="
Test-Tool "clang"    @("--version")
Test-Tool "clang++"  @("--version")
Test-Tool "clang-cl" @("--version")
Test-Tool "lld"      @("--version")
Test-Tool "ld.lld"   @("--version")
Test-Tool "llvm-ar"  @("--version")

# ── Go ────────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "=== Go ==="
Test-Tool "go"     @("version")
Test-Tool "gofmt"  @("-l")

# ── Rust ──────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "=== Rust ==="
Test-Tool "rustc"    @("--version")
Test-Tool "cargo"    @("--version")
Test-Tool "rustup"   @("--version")
Test-Tool "rustfmt"  @("--version")
Test-Tool "clippy-driver" @("--version")

# ── Python ───────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "=== Python ==="
Test-Tool "python"   @("--version")
Test-Tool "python3"  @("--version")
Test-Tool "pip"      @("--version")
Test-Tool "pip3"     @("--version")
Test-Tool "pipx"     @("--version")

# ── Node.js ───────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "=== Node.js ==="
Test-Tool "node"   @("--version")
Test-Tool "npm"    @("--version")
Test-Tool "npx"    @("--version")
Test-Tool "yarn"   @("--version")

# ── .NET ──────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "=== .NET ==="
Test-Tool "dotnet"  @("--version")

# ── Java ──────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "=== Java ==="
Test-Tool "java"   @("-version")
Test-Tool "javac"  @("-version")
Test-Tool "jar"    @("--version")

# ── Other languages ───────────────────────────────────────────────────────────
Write-Host ""
Write-Host "=== Other languages ==="
Test-Tool "kotlin"   @("-version")
Test-Tool "php"      @("--version")
Test-Tool "ruby"     @("--version")
Test-Tool "perl"     @("--version")

# ── Package managers ──────────────────────────────────────────────────────────
Write-Host ""
Write-Host "=== Package managers ==="
Test-Tool "composer"  @("--version")
Test-Tool "gem"       @("--version")
Test-Tool "nuget"     @("help")
Test-Tool "vcpkg"     @("version")
Test-Tool "helm"      @("version")

# ── Result ────────────────────────────────────────────────────────────────────
Write-Host ""
if ($failed -ne 0) {
    Write-Host "Some checks failed!"
    exit 1
}
Write-Host "All checks passed."
