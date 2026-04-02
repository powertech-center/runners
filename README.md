# PowerTech Runners

Reusable GitHub Actions jobs covering **8 target platforms** with fast prebuilt environments — consistent toolset, minimal configuration.

## Usage

Reference one of the 8 target jobs in your workflow: `linux-x64-gnu`, `linux-x64-musl`, `linux-arm64-gnu`, `linux-arm64-musl`, `windows-x64`, `windows-arm64`, `macos-x64`, `macos-arm64`.

```yaml
jobs:
  # Linux GNU — build and upload artifact
  build:
    uses: powertech-center/runners/.github/workflows/linux-x64-gnu.yml@main
    with:
      command: |
        cmake -B build --toolchain cmake/windows-x64.cmake
        cmake --build build
      artifacts-upload: "my-app"

  # Windows — pwsh shell (cmd and bash are also available), download artifact
  test:
    needs: build
    uses: powertech-center/runners/.github/workflows/windows-x64.yml@main
    with:
      shell: pwsh
      command: "./artifacts/my-app/my-app.exe --run-tests"
      artifacts-download: "my-app"

  # macOS — downloads artifact, uploads multiple release packages
  package:
    needs: test
    uses: powertech-center/runners/.github/workflows/macos-arm64.yml@main
    with:
      command: "./scripts/sign-and-package.sh"
      artifacts-download: "my-app"
      artifacts-upload: "my-app.msi my-app.zip my-app.tar.gz"
```

## Parameters

| Input | Required | Default | Description |
|---|---|---|---|
| `name` | No | `{platform}` | Job display name |
| `checkout` | No | `true` | Checkout the calling repository |
| `checkout-submodules` | No | `false` | Checkout submodules recursively |
| `checkout-lfs` | No | `false` | Fetch LFS objects |
| `shell` | No | `bash` | Shell to execute the command in (`bash`, `sh`, `pwsh`, `cmd`) |
| `command` | Yes | — | Command to execute |
| `artifacts-dir` | No | `artifacts` | Root directory for artifacts |
| `artifacts-download` | No | `""` | Space-separated artifact names to download into `{artifacts-dir}/{name}` before `command` |
| `artifacts-upload` | No | `""` | Space-separated artifact names to upload from `{artifacts-dir}/{name}` after `command` |
| `timeout` | No | `30` | Job timeout in minutes |

## What's Included

All 8 platforms share a consistent, pre-configured toolset. Anything missing on a specific runner is installed automatically during bootstrap. See [runner-images](https://github.com/actions/runner-images) for the full list.

**Core utilities**
`bash`, `pwsh`, `git`, `git-lfs`, `curl`, `wget`, `aria2`, `tar`, `xz`, `zstd`, `zip`, `unzip`, `7z`, `rsync`, `gpg`, `gh`, `aws`, `az`

**Text processing**
`jq`, `yq`, `grep`, `sed`, `awk`, `find`, `tree`

**Build systems**
`cmake`, `ninja`, `make`, `pkg-config`, `gradle`, `maven`, `ant`, `bazel`

**Compilers & runtimes**
`gcc`, `clang`, `go`, `rustc/cargo`, `python`, `node`, `dotnet`, `java`, `kotlin`, `php`, `ruby`, `perl`

**Package managers**
`pip`, `npm`, `yarn`, `composer`, `gem`, `nuget`, `vcpkg`, `helm`, `pipx`

## Environment Variables

All standard GitHub Actions environment variables are available — `GITHUB_*` for repository and workflow context, `RUNNER_*` for runner details. If you're not familiar with them, see the [GitHub docs](https://docs.github.com/en/actions/reference/workflows-and-actions/variables).

In addition, one variable is set by the runner itself:

| Variable | Example | Description |
|---|---|---|
| `RUNNER_PLATFORM` | `linux-x64-gnu` | The target platform selected for this job — useful for writing cross-platform scripts that need to branch on OS, architecture, or libc. |

## Bootstrap

GitHub-hosted runners already come with an impressive set of tools — kudos to the GitHub Actions team for that. However, each runner differs slightly in what's available out of the box. To give users a truly consistent experience, we took on the task of maintaining a unified toolset across all platforms, and called this process *bootstrap*.

Instead of installing missing tools at runtime via package managers like `brew`, `choco`, or `apt` — which can take tens of seconds or even minutes — we use a different approach. A dedicated workflow (`bootstrap-oci.yml`) runs on a schedule to stay up to date: it spins up each runner, brings it to the desired state, packages the diff as a patch, and publishes it via GitHub Packages. At job startup, bootstrap downloads and applies this patch in just a couple of seconds.

On Linux and Windows, we install LLVM runtime libraries (`libc++`, `compiler-rt`) including headers and static variants, which are absent by default. On macOS, `go` is present on the runner but not activated — we add it to PATH; we also install `tree`, and on arm64 — `php` and `composer`. On Windows, we additionally provide `wget`, `zip`, and `yq`; on arm64 we also build and bundle `zstd`.

Linux musl targets deserve a special mention. GitHub doesn't provide Alpine runners, and the obvious solution — a Docker image — is too slow (pulling approaches a minute). Chroot cuts off the host toolset; wrappers bridging Alpine into the host didn't work well either. We ended up with a hybrid *pseudo-Alpine*: the host runner's glibc-based utilities stay intact, unnecessary bits are stripped, and a full Alpine shell is layered on top — `apk`, `/etc/alpine-release`, BusyBox-backed `sed` and `grep`, and so on. Tools like `apt-get` and `lsb-release` are renamed with an underscore prefix rather than removed, so they're recoverable if needed.

Compilers are handled separately: the GCC toolchain is replaced with a musl-targeting build, while Clang gets smart wrappers that behave as musl-targeted but are themselves glibc-linked — no full reinstall required.

## License

MIT
