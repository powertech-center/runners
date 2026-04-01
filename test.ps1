$failed = 0

Write-Host "=== Runner environment ==="
foreach ($name in @("RUNNER_PLATFORM", "RUNNER_OS", "RUNNER_ARCH", "RUNNER_NAME")) {
    $value = [Environment]::GetEnvironmentVariable($name)
    if ([string]::IsNullOrEmpty($value)) {
        Write-Host "${name}: <not set>"
    } else {
        Write-Host "${name}: $value"
    }
}

Write-Host ""
Write-Host "=== Core utilities ==="
foreach ($cmd in @("bash", "git", "git-lfs", "curl", "wget", "tar", "xz", "zstd", "zip", "unzip", "7z")) {
    if (Get-Command $cmd -ErrorAction SilentlyContinue) {
        Write-Host "OK: $cmd"
    } else {
        Write-Host "FAIL: $cmd not found"
        $failed = 1
    }
}

Write-Host ""
Write-Host "=== Text processing & search ==="
foreach ($cmd in @("jq", "yq", "grep", "sed", "find", "tree")) {
    if (Get-Command $cmd -ErrorAction SilentlyContinue) {
        Write-Host "OK: $cmd"
    } else {
        Write-Host "FAIL: $cmd not found"
        $failed = 1
    }
}

Write-Host ""
Write-Host "=== Build systems ==="
foreach ($cmd in @("make", "cmake", "ninja", "pkg-config")) {
    if (Get-Command $cmd -ErrorAction SilentlyContinue) {
        Write-Host "OK: $cmd"
    } else {
        Write-Host "FAIL: $cmd not found"
        $failed = 1
    }
}

Write-Host ""
Write-Host "=== Compilers ==="
foreach ($cmd in @("gcc", "g++", "clang")) {
    if (Get-Command $cmd -ErrorAction SilentlyContinue) {
        Write-Host "OK: $cmd"
    } else {
        Write-Host "FAIL: $cmd not found"
        $failed = 1
    }
}

Write-Host ""
Write-Host "=== Languages & runtimes ==="
foreach ($cmd in @("python3", "python", "perl", "php", "ruby", "node", "npm", "go", "rustc", "cargo", "dotnet", "java", "pwsh")) {
    if (Get-Command $cmd -ErrorAction SilentlyContinue) {
        Write-Host "OK: $cmd"
    } else {
        Write-Host "FAIL: $cmd not found"
        $failed = 1
    }
}

Write-Host ""
Write-Host "=== Node.js package managers ==="
foreach ($cmd in @("yarn")) {
    if (Get-Command $cmd -ErrorAction SilentlyContinue) {
        Write-Host "OK: $cmd"
    } else {
        Write-Host "FAIL: $cmd not found"
        $failed = 1
    }
}

Write-Host ""
Write-Host "=== PHP ecosystem ==="
foreach ($cmd in @("composer")) {
    if (Get-Command $cmd -ErrorAction SilentlyContinue) {
        Write-Host "OK: $cmd"
    } else {
        Write-Host "FAIL: $cmd not found"
        $failed = 1
    }
}

if ($failed -ne 0) {
    Write-Host ""
    Write-Host "Some checks failed!"
    exit 1
}

Write-Host ""
Write-Host "All checks passed."