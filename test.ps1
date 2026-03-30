$failed = 0

Write-Host "=== Pre-installed utilities ==="
foreach ($cmd in @("bash", "git", "curl", "wget", "zip", "unzip", "tar", "gzip", "make", "ninja", "pkg-config")) {
    if (Get-Command $cmd -ErrorAction SilentlyContinue) {
        Write-Host "OK: $cmd"
    } else {
        Write-Host "FAIL: $cmd not found"
        $failed = 1
    }
}

Write-Host ""
Write-Host "=== Tools ==="
foreach ($cmd in @("gcc", "clang", "cmake", "meson", "go", "rustc", "dotnet", "node", "java", "gradle", "flutter", "crossler", "pwsh")) {
    if (Get-Command $cmd -ErrorAction SilentlyContinue) {
        Write-Host "OK: $cmd"
    } else {
        Write-Host "FAIL: $cmd not found"
        $failed = 1
    }
}

if (Get-Command python3 -ErrorAction SilentlyContinue) {
    Write-Host "OK: python3"
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    Write-Host "OK: python"
} else {
    Write-Host "FAIL: python not found"
    $failed = 1
}

if ($failed -ne 0) {
    Write-Host ""
    Write-Host "Some checks failed!"
    exit 1
}

Write-Host ""
Write-Host "All checks passed."
