# PowerTech Runners

Reusable GitHub Actions jobs for cross-platform CI on **8 target platforms**. No need to configure runners, install dependencies, or write platform-specific scripts — just specify your command and go.

## Supported Platforms

| Platform | Runner | Shell |
|---|---|---|
| `linux-x64-gnu` | `ubuntu-24.04` | `bash` |
| `linux-x64-musl` | `ubuntu-24.04` + `alpine:latest` | `bash` |
| `linux-arm64-gnu` | `ubuntu-24.04-arm` | `bash` |
| `linux-arm64-musl` | `ubuntu-24.04-arm` + `alpine:latest` | `bash` |
| `windows-x64` | `windows-2025` | `pwsh` |
| `windows-arm64` | `windows-11-arm` | `pwsh` |
| `macos-x64` | `macos-15` | `bash` |
| `macos-arm64` | `macos-15` | `bash` |

All platforms run on native GitHub-hosted runners (including arm64). Musl platforms use an Alpine container on top of the Ubuntu runner. `macos-x64` runs on an Apple Silicon runner under Rosetta 2 emulation (`PT_EMULATED=true`), as GitHub has retired Intel-based macOS runners.

## Quick Start

### Single platform

```yaml
jobs:
  test:
    uses: powertech-center/runners/.github/workflows/linux-x64-gnu.yml@main
    with:
      command: |
        cmake -B build
        cmake --build build
        ctest --test-dir build
      tools: "gcc cmake"
```

### Multiple platforms

```yaml
jobs:
  linux:
    uses: powertech-center/runners/.github/workflows/linux-x64-gnu.yml@main
    with:
      command: "./scripts/test.sh"
      tools: "gcc cmake"

  alpine:
    uses: powertech-center/runners/.github/workflows/linux-x64-musl.yml@main
    with:
      command: "./scripts/test.sh"
      tools: "gcc cmake"

  windows:
    uses: powertech-center/runners/.github/workflows/windows-x64.yml@main
    with:
      command: "./scripts/test.ps1"

  macos:
    uses: powertech-center/runners/.github/workflows/macos-arm64.yml@main
    with:
      command: "./scripts/test.sh"
      tools: "cmake"
```

### With build artifacts

```yaml
jobs:
  build:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v4
      - run: ./build.sh
      - uses: actions/upload-artifact@v4
        with:
          name: myapp-linux-x64-gnu
          path: dist/

  test:
    needs: build
    uses: powertech-center/runners/.github/workflows/linux-x64-gnu.yml@main
    with:
      command: "./artifacts/run-tests.sh"
      artifact-name-prefix: myapp
      checkout: false
```

## Parameters

| Input | Required | Default | Description |
|---|---|---|---|
| `command` | Yes | — | Command to execute |
| `name` | No | `Run ({platform})` | Job display name |
| `tools` | No | `""` | Space-separated list of tools to install |
| `checkout` | No | `true` | Checkout the calling repository |
| `artifact-name-prefix` | No | `""` | Base artifact name (`{prefix}-{platform}`) |
| `artifact-path` | No | `./artifacts` | Path to download artifacts to |
| `timeout-minutes` | No | `30` | Job timeout in minutes |

## Pre-installed Utilities

Every platform comes with a set of common utilities out of the box — no need to list them in `tools`:

`bash`, `git`, `curl`, `wget`, `zip`, `unzip`, `tar`, `gzip`, `make`, `ninja`, `pkg-config`

These are either pre-installed on the runner or automatically added during the bootstrap step.

## Tools

Use the `tools` parameter to install additional compilers, languages, and build systems:

```yaml
tools: "gcc cmake python"
```

### Compilers

| Tool | Description |
|---|---|
| `gcc` | GNU Compiler Collection (+ build-essential/build-base) |
| `clang` | LLVM/Clang compiler |

### Build Systems

| Tool | Description |
|---|---|
| `cmake` | CMake build system generator |
| `meson` | Meson build system |

### Languages & Runtimes

| Tool | Description |
|---|---|
| `python` | Python 3 |
| `go` | Go |
| `rust` | Rust (rustc + cargo) |
| `dotnet` | .NET SDK |
| `node` | Node.js + npm |
| `java` | Java (Temurin JDK 21) |
| `gradle` | Gradle build tool |
| `flutter` | Flutter SDK |
| `pwsh` | PowerShell (Linux/macOS only, pre-installed on Windows) |

### Other

| Tool | Description |
|---|---|
| `crossler` | [Crossler](https://github.com/powertech-center/crossler) cross-compilation tool |

## Environment Variables

The following environment variables are available in your command:

| Variable | Example | Description |
|---|---|---|
| `PT_PLATFORM` | `linux-x64-gnu` | Full platform triplet |
| `PT_OS` | `linux` | Operating system |
| `PT_ARCH` | `x64` | CPU architecture |
| `PT_LIBC` | `gnu` | C library (Linux only) |
| `PT_EMULATED` | `false` | Running under emulation |

## Artifact Naming Convention

Artifacts follow the pattern: **`{prefix}-{platform}`**

Examples: `mylib-linux-x64-gnu`, `compiler-windows-x64`, `sdk-macos-arm64`

## License

MIT
