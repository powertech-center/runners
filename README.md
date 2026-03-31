# PowerTech Runners

Reusable GitHub Actions jobs for cross-platform CI on **8 target platforms**. No need to configure runners, install dependencies, or write platform-specific scripts — just specify your command and go.

## Supported Platforms

| Platform | Runner | Shell |
|---|---|---|
| `linux-x64-gnu` | `ubuntu-24.04` | `bash` |
| `linux-x64-musl` | `ubuntu-24.04` + `alpine:latest` | `bash` |
| `linux-arm64-gnu` | `ubuntu-24.04-arm` | `bash` |
| `linux-arm64-musl` | `ubuntu-24.04-arm` + `alpine:latest` | `bash` |
| `windows-x64` | `windows-latest` | `pwsh` |
| `windows-arm64` | `windows-11-arm` | `pwsh` |
| `macos-x64` | `macos-26-intel` | `bash` |
| `macos-arm64` | `macos-26` | `bash` |

All platforms run on native GitHub-hosted runners (including arm64). Musl platforms use an Alpine container on top of the Ubuntu runner.

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
      tools: "gcc"
```

### Multiple platforms

```yaml
jobs:
  linux:
    uses: powertech-center/runners/.github/workflows/linux-x64-gnu.yml@main
    with:
      command: "./scripts/test.sh"
      tools: "gcc"

  alpine:
    uses: powertech-center/runners/.github/workflows/linux-x64-musl.yml@main
    with:
      command: "./scripts/test.sh"
      tools: "gcc"

  windows:
    uses: powertech-center/runners/.github/workflows/windows-x64.yml@main
    with:
      command: "./scripts/test.ps1"

  macos:
    uses: powertech-center/runners/.github/workflows/macos-arm64.yml@main
    with:
      command: "./scripts/test.sh"
```

### With artifacts

```yaml
jobs:
  build:
    uses: powertech-center/runners/.github/workflows/linux-x64-gnu.yml@main
    with:
      command: |
        ./build.sh
        cp -r dist/* artifacts/my-build/
      artifacts-upload: "my-build"
      tools: "gcc"

  test:
    needs: build
    uses: powertech-center/runners/.github/workflows/linux-x64-gnu.yml@main
    with:
      command: "./artifacts/my-build/run-tests.sh"
      artifacts-download: "my-build"
```

You can download multiple artifacts from previous jobs:

```yaml
  integration:
    needs: [build-lib, build-app]
    uses: powertech-center/runners/.github/workflows/linux-x64-gnu.yml@main
    with:
      command: "./run-integration.sh"
      artifacts-download: "lib-output app-output"
```

## Parameters

| Input | Required | Default | Description |
|---|---|---|---|
| `name` | No | `{platform}` | Job display name |
| `checkout` | No | `true` | Checkout the calling repository |
| `tools` | No | `""` | Space-separated list of tools to install |
| `command` | Yes | — | Command to execute |
| `artifacts-dir` | No | `artifacts` | Directory for artifacts |
| `artifacts-download` | No | `""` | Space-separated list of artifact names to download before `command` |
| `artifacts-upload` | No | `""` | Space-separated list of artifact names to upload after `command` |
| `timeout` | No | `30` | Job timeout in minutes |

## Pre-installed Utilities

Every platform comes with a set of common utilities out of the box — no need to list them in `tools`:

`bash`, `git`, `curl`, `wget`, `zip`, `unzip`, `tar`, `gzip`, `make`, `ninja`, `pkg-config`, `cmake`, `pwsh`, `python`

These are either pre-installed on the runner or automatically added during the bootstrap step.

## Tools

Use the `tools` parameter to install additional compilers, languages, and runtimes:

```yaml
tools: "gcc go"
```

### Compilers

| Tool | Description |
|---|---|
| `gcc` | GNU Compiler Collection (+ build-essential/build-base) |
| `clang` | LLVM/Clang compiler |

### Languages & Runtimes

| Tool | Description |
|---|---|
| `go` | Go |
| `rust` | Rust (rustc + cargo) |
| `dotnet` | .NET SDK |
| `nodejs` | Node.js + npm |
| `java` | Java (Temurin JDK 21) |
| `gradle` | Gradle build tool |
| `flutter` | Flutter SDK |

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

## Artifacts

Artifacts are stored as files or directories inside `artifacts-dir` (default: `artifacts`). Each name in `artifacts-download` / `artifacts-upload` corresponds to a subdirectory or file inside that directory.

- **Download** happens before `command` — each name is downloaded into `{artifacts-dir}/{name}/`
- **Upload** happens after `command` — each name is uploaded from `{artifacts-dir}/{name}`
- The mechanism works identically on all 8 platforms (including musl) via REST API

## License

MIT
