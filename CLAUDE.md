# CLAUDE.md

Переиспользуемые GitHub Actions workflows для кросс-платформенного CI на 8 целевых платформах (linux/windows/macos, x64/arm64, gnu/musl).

## Структура

- `.github/workflows/` — по одному reusable workflow на платформу (напр. `linux-x64-gnu.yml`)
- `.github/workflows/bootstrap-build-publish.yml` — сборка и публикация OCI-архивов для bootstrap
- `.github/workflows/bootstrap-test.yml` — запуск bootstrap-test.py на всех 8 платформах
- `run/action.yml` — единый composite action для всех платформ
- `bootstrap-build.py` — формирование bundle-директории для OCI-архивов (вызывается из bootstrap-build-publish.yml)
- `bootstrap-test.py` — единый тестовый скрипт, покрывающий все платформы

## Bootstrap

Унифицированная концепция для всех 8 платформ: **prepare → download → extract to root**.

1. **Prepare** (опционально) — подготовка пространства перед распаковкой (напр. снос конфликтующих файлов)
2. **Download** — скачивание OCI-архива из `ghcr.io/powertech-center/runners/{platform}:latest`
3. **Extract to root** — распаковка архива в корень файловой системы (`/` на Unix, `C:\` на Windows)

Архив содержит полные пути от корня — никакого `--strip-components` или целевых директорий. Примеры содержимого:
- macOS: `opt/homebrew/Cellar/...`, `opt/homebrew/bin/...`
- Windows: `ProgramData/Chocolatey/bin/...`
- linux-gnu: `usr/lib/...`, `usr/include/...`
- linux-musl: `lib/ld-musl-*.so.1`, `usr/bin/...`, `usr/include/...`, `etc/os-release`, `etc/apk/...`

## Ключевые концепции

- Каждый workflow — тонкая `workflow_call` обёртка, делегирующая в composite action `run/`
- Никакие JS actions не используются — весь checkout, артефакты и bootstrap реализованы через shell
- Checkout: shell-based `git fetch --depth=1` + `git checkout FETCH_HEAD`, поддержка submodules и LFS через inputs
- Артефакты управляются через `artifacts-dir`, `artifacts-download`, `artifacts-upload` (REST API)
- Формат триплета платформы: `{os}-{arch}-{libc}` (напр. `linux-x64-gnu`, `windows-x64`, `macos-arm64`)

## При редактировании

- Сигнатуры inputs должны быть идентичны во всех 8 workflow
- Изменения тестируются через `bootstrap-test.yml`, который запускает все платформы
- Actions ссылаются как `powertech-center/runners/run@main`

## Workflow разработка и анализ

**Протокол работы с GitHub Actions в этом репо:**

1. **Запуск workflows**: 
   - Во время разработки интересующие нас workflows (диагностические, критичные) ставятся на триггер по `push` — это позволяет видеть результаты сразу после каждого коммита.
   - Примеры: `audit-toolchain.yml`, `bootstrap-build-publish.yml` (iteration 6 добавил гейтинг build → emulate → test → publish).
   - Workflows **запускаются пользователем**, не Claude.

2. **Анализ результатов**:
   - Пользователь предоставляет Claude ссылку на завершённый workflow run (например, `https://github.com/powertech-center/runners/actions/runs/24280737069`).
   - Claude использует `gh run view <run-id>` и `gh run view --job <job-id> --log` для чтения логов **через GitHub API** (не вручную).
   - Логи парсятся в памяти, результаты синтезируются и предоставляются пользователю.

3. **Инструментарий**:
   - `gh run list --workflow=<name>.yml --limit N` — получить список последних N runs конкретного workflow
   - `gh run view <run-id>` — summary по jobs (статусы, время выполнения)
   - `gh run view --job <job-id> --log` — полный лог одного job'а
   - Требует: `GITHUB_TOKEN` (PAT) с правами `Actions: Read` и `Metadata: Read`; если token fine-grained, lifetime должен быть ≤ 366 дней (политика orge `powertech-center`).

4. **Примеры диагностики**:
   - `bootstrap-build-publish.yml` — полный цикл build → emulate → test → publish для каждой платформы. Если тесты на какой-то платформе упали, публикация не происходит (гейтинг). Триггер: `workflow_dispatch` (запускается вручную, когда меняется `bootstrap-build.py` или состав bundle).
   - `bootstrap-test.yml` — пост-publish валидация последних опубликованных bundle на холодных раннерах. Триггер: `push` (во время активной разработки action-а) + `workflow_dispatch` + weekly `schedule` (понедельник 06:00 UTC) — еженедельный smoke-тест ловит дрифт раннер-образов между публикациями.
   - Исторически был `audit-toolchain.yml` + `audit-toolchain.py` для one-shot диагностики runtime toolchain (compiler versions, libc++ availability, compile probes, Alpine minirootfs probe). Удалён после того, как все выводы зафиксированы в секции «Findings» ниже.

5. **Паттерн переключения workflows при разработке платформы**:
   - **Фаза отладки** (build + publish новой платформы): в `bootstrap-build-publish.yml` ставим `on: push:`, в matrix оставляем только отлаживаемую платформу (остальные закомментированы). `bootstrap-test.yml` в это время без `push:` — чтобы не гонять холодные тесты при каждом коммите.
   - **Фаза валидации** (после успешного publish): в `bootstrap-build-publish.yml` убираем `push:` (остаётся только `workflow_dispatch`), в matrix возвращаем все стабильные платформы. В `bootstrap-test.yml` наоборот ставим `on: push:` и раскомментируем отлаженные платформы — чтобы каждый коммит прогонял cold bootstrap test на всех стабильных бандлах.
   - Таким образом, `push:` триггер всегда стоит **ровно на одном** из двух workflows, в зависимости от фазы.

## bootstrap-build.py — статус и план разработки

`bootstrap-build.py` находится в активной разработке. Это скрипт, формирующий ROOT-директорию для каждой из 8 платформ, которая затем упаковывается в один `tar.gz` blob и публикуется как OCI-артефакт в `ghcr.io/powertech-center/runners/{platform}:latest`. При bootstrap (`run/action.yml`) этот архив скачивается и распаковывается в корень файловой системы целевого раннера.

### Контракт скрипта

- **CLI**: `python3 bootstrap-build.py <platform> [--root <dir>]`
- **ROOT по умолчанию**: `$RUNNER_TEMP/bootstrap-root`, fallback `<repo>/.tmp/bootstrap-root` (если `RUNNER_TEMP` не задан — для локальной разработки)
- **Что делает**: создаёт ROOT-директорию, наполняет её файлами от корня ФС (`opt/homebrew/...`, `ProgramData/Chocolatey/bin/...`, `usr/lib/...`), упаковывает в `patch.tar.gz`. Публикацию делает уже `bootstrap-build-publish.yml`.
- **ROOT — это не sysroot**, а именно набор файлов с путями от корня, готовых к распаковке `tar -xzf patch.tar.gz -C /` (или `C:\`).

### Формат публикации — единственный blob, без манифеста

`run/action.yml` берёт `layers[0].digest` из OCI-манифеста и распаковывает первый blob как `tar.gz` в корень. **Никакой `meta/manifest.json` не читается** — это был мёртвый артефакт от прошлой архитектуры. В новой реализации мы не генерируем манифестов и не пушим несколько слоёв: ровно один `patch.tar.gz` через `oras push`.

### Гейтинг публикации

`bootstrap-build-publish.yml` для каждой платформы в matrix делает:
1. Build — вызов `bootstrap-build.py <platform>`
2. Emulate bootstrap — распаковка свежесобранного `patch.tar.gz` в корень текущего раннера
3. Test — запуск `bootstrap-test.py`
4. Publish — `oras push` в GHCR **только если test green**

### Содержимое bundle по платформам

- **macOS**: новые brew-пакеты (по diff `brew list` до/после) — `opt/homebrew/Cellar/<pkg>/...` + симлинки в `opt/homebrew/{bin,lib,include,share,etc}/` + `opt/homebrew/opt/<pkg>`. libc++ **не нужен** (есть в Xcode SDK).
- **Windows**: standalone утилиты (wget, yq, zstd, ld.lld) в `ProgramData/Chocolatey/bin/` + MSYS2 tools (zip, rsync, tree, pkgconf) через pacman snapshot diff в `msys64/...` + копии .exe/.dll в `ProgramData/Chocolatey/bin/` для PATH-видимости + libc++ для clang в `Program Files/LLVM/{include,lib}/`.
- **Linux gnu**: то, чего нет на ubuntu-24.04 раннере, + libc++ для clang в `usr/include/c++/v1/` и `usr/lib/{x86_64,aarch64}-linux-gnu/`.
- **Linux musl**: всё из pseudo-alpine overlay (musl линкер, apk, alpine identity, ldd-wrapper, gcc через apk, clang/rust wrappers) + libc++ в `usr/include/c++/v1/` и `usr/lib/`.

### libc++ — отдельная подсистема

Нужен для всех платформ кроме macOS. Собирается **нативно на каждом раннере** (не кросс-компиляция, как в `C:\PowerTech\Dockers\cross-clang`) из исходников `llvm-project/runtimes` через CMake. Конфигурация различается по платформам:
- Linux gnu: `LIBCXX_ENABLE_STATIC=ON`, `LIBCXXABI_USE_LLVM_UNWINDER=ON`, install в `/usr`.
- Linux musl: дополнительно `LIBCXX_HAS_MUSL_LIBC=ON`, `LIBCXXABI_HAS_CXA_THREAD_ATEXIT_IMPL=OFF`.
- Windows: install в `Program Files/LLVM`, нужен LLVM clang на хосте.

Эталон CMake-флагов: `C:\PowerTech\Dockers\cross-clang\Dockerfile.template` Stage 3 (строки 240–397) — но там кросс-компиляция, нам нужно убрать `--target=`, `CMAKE_SYSROOT`, `linker_flags`. `bootstrap-test.py` должен иметь сценарии, проверяющие компиляцию с `-stdlib=libc++ -static`.

### Источники логики (что переносим)

| Фрагмент | Откуда | Объём |
|---|---|---|
| Windows: download wget/zstd/zip/yq | текущий `bootstrap-build-publish.yml` (Windows секция, PowerShell) | ~150 строк |
| macOS: brew snapshot diff | текущий `bootstrap-build-publish.yml` (macOS секция, bash) | ~150 строк |
| Linux musl: overlay (apk, identity, ldd, wrappers) | `C:\PowerTech\pseudo-alpine\init.sh` + `docker/2-overlay/setup.sh` + `docker/3-wrappers/setup.sh` + `wrappers/*.in` | ~600 строк sh |
| libc++ build | `C:\PowerTech\Dockers\cross-clang\Dockerfile.template` Stage 3 | ~150 строк (после удаления кросс-частей) |
| Динамический подбор Alpine версии | `C:\PowerTech\pseudo-alpine\scripts\find-alpine-version.py` | как есть |

### План реализации (итерации с коммитами)

Каждая итерация — отдельный коммит. После каждой прогоняем `bootstrap-build-publish.yml` в CI и смотрим зелёное/красное.

1. **Каркас + windows-x64** — DONE. `bootstrap-build.py` создан с CLI, dispatcher, helpers (download/run/pack_tar_gz), windows-x64 переносит PS-логику (wget, yq, zstd via cmake/VS, zip via MSYS2). `bootstrap-build-publish.yml` переписан на новую модель: matrix только с windows-x64, вызов `python3 bootstrap-build.py <platform> --pack`, публикация одним blob `patch.tar.gz` через oras. Остальные платформы закомментированы.
2. **windows-arm64 + macos-x64 + macos-arm64** — DONE. windows-arm64 переиспользует ту же `build_windows()` с другим arch. Для macOS добавлена `build_macos()`: detect missing → brew install → snapshot diff → копирование Cellar/<pkg>, prefix симлинков (bin/sbin/lib/include/share/etc) и opt/<pkg> в ROOT с правильным абсолютным префиксом (`opt/homebrew/...` на arm64, `usr/local/...` на x64). Это исправляет ошибку legacy-кода, который клал bundle в `$BUNDLE/Cellar/` без префикса от корня. Matrix в `bootstrap-build-publish.yml` расширен windows-arm64 + обоими macOS.
3. **linux-x64-gnu + linux-arm64-gnu** — DONE. На ubuntu-24.04 уже всё есть, поэтому `build_linux_gnu()` пока только аудитит набор ожидаемых тулов и оставляет ROOT пустым (это валидный исход). `pack_tar_gz()` доработан: если ROOT пустой, добавляется маркер `.bootstrap-empty`, чтобы oras push получил непустой blob. Реальная заливка libc++ начнётся в итерации 4. Matrix расширен linux-x64-gnu + linux-arm64-gnu.
4. **libc++ для всех Linux/Windows + тесты** — DONE. Добавлена секция libc++ subsystem с диспетчером `install_libcxx(platform, root)`. Стратегии:
   - **Linux gnu**: versioned `apt-get install libc++-{N}-dev libc++abi-{N}-dev libunwind-{N}-dev` (major `N` детектится из `clang --version`), копирование `/usr/include/c++/v1/` и **реальных** libc++/libc++abi/libunwind из `/usr/lib/llvm-{N}/lib/` + поимённых forwarding-симлинков из `/usr/lib/<triplet>/`. См. Findings #1 про layout noble (реальные файлы живут в `llvm-{N}/lib/`, триплет содержит только симлинки и параллельно — несовместимый системный libunwind.so.8).
   - **Windows**: загрузка llvm-project sources (`LIBCXX_LLVM_VERSION` env var, дефолт 19.1.7), сборка `runtimes` через CMake/Ninja с native clang/clang++, install в `Program Files/LLVM/`. Долго (~минуты), но единственный надёжный способ получить static libc++ под Windows.
   - **macOS**: no-op (libc++ в Xcode SDK).
   - **Linux musl**: deferred в итерацию 5 (через apk).
   `install_libcxx()` вызывается в конце `build_windows()` и `build_linux_gnu()`. `bootstrap-test.py` group_libcxx переписан: 3 теста (basic compile/run, vector+algorithm с проверкой output, static linking на Linux). Тесты не пропускают Windows как раньше — теперь они валидируют, что bundle действительно работает.
5. **linux-x64-musl + linux-arm64-musl** — DONE. Реализация в `build_linux_musl(arch, root)` через подход «Alpine minirootfs внутрь ROOT»:
   - Скачивается `alpine-minirootfs-<ver>-<arch>.tar.gz` с `dl-cdn.alpinelinux.org` (бранч `latest-stable`, версия и branch берутся из `latest-releases.yaml` — парсер в `_alpine_minirootfs_url()` ищет `flavor: alpine-minirootfs` и соответствующий `branch:`).
   - Архив распаковывается **прямо в ROOT** — после этого `ROOT/lib/ld-musl-*.so.1`, `ROOT/sbin/apk`, `ROOT/etc/os-release` и пр. лежат с правильными абсолютными путями от корня.
   - В `ROOT/etc/alpine-release` пишется версия, в `ROOT/usr/bin/ldd` — shim вызывающий `/lib/ld-musl-<arch>.so.1 --list`, в `ROOT/etc/apk/repositories` — main + community текущего бранча.
   - Затем **apk из самого minirootfs** (`ROOT/sbin/apk` — статический бинарь из Alpine) запускается с `--root <ROOT> --initdb --no-scripts add musl-dev gcc g++ libstdc++-dev libc++-dev libc++-static llvm-libunwind-static compiler-rt`. Это устанавливает пакеты внутрь нашего bundle, не затрагивая хост. Никакого `apt-get install apk-tools` на Ubuntu не нужно — в noble universe этого пакета всё равно нет, а minirootfs уже содержит всё необходимое. `_apk_install_into_root()` сначала ищет `ROOT/sbin/apk.static`/`ROOT/sbin/apk`, потом — хостовый apk как fallback.
   - libc++ для musl приходит из самого Alpine (`libc++-static` v18 в Alpine 3.21 — matching host clang-18), отдельная сборка не нужна — `install_libcxx` для musl остаётся no-op'ом. Alpine pinned **v3.21** (не latest-stable), потому что Alpine 3.23 ставит libc++ v21, несовместимый с `clang-18` (host). Пакета `libc++abi-static` в Alpine нет — символы `libc++abi.a` упакованы внутри `libc++-static`.
   - **Clang wrapper для `-stdlib=libc++ -static`**: clang driver по умолчанию `--rtlib=libgcc` тянет `libgcc_eh.a`, которая конфликтует по символам `_Unwind_*` с LLVM `libunwind.a`. Обёртка детектит пару `-stdlib=libc++ -static` и подставляет `--rtlib=compiler-rt --unwindlib=libunwind` (pre-args) + `-lc++abi` (post-arg, потому что GNU bfd `/usr/bin/ld` линкует строго в порядке и не группирует libc++ с libc++abi).
   - **Не перенесено** (deferred): clang/rust wrappers из `pseudo-alpine/wrappers/*.in` (нужны если консьюмер будет использовать clang/rustc по умолчанию вместо gcc) и динамический выбор Alpine-бранча по версии хостового GCC из `find-alpine-version.py` (используется `latest-stable`).
   - Matrix в `bootstrap-build-publish.yml` расширен `linux-x64-musl` + `linux-arm64-musl`.
6. **Gating workflow** — DONE. `bootstrap-build-publish.yml` финализирован как **build → emulate bootstrap → test → publish**. После шага сборки добавлены два варианта эмуляции (`Emulate bootstrap (Unix)` через `sudo tar -xzf $RUNNER_TEMP/patch.tar.gz -C /` и `Emulate bootstrap (Windows)` через `tar -xzf %RUNNER_TEMP%\patch.tar.gz -C C:\`) — точная копия того, что делает `run/action.yml` у консьюмеров, только применённая к свежесобранному архиву на самом раннере. Затем шаг `Run bootstrap tests (gating)` запускает `python3 bootstrap-test.py` с экспортированным `RUNNER_PLATFORM`. Если тесты падают — job фейлится, и шаги `Install ORAS` / `Publish OCI artifact` не выполняются. Это гарантирует, что в GHCR никогда не уезжает сломанный bundle. Для пустого ROOT (linux gnu) распаковка `.bootstrap-empty` маркера в `/` — no-op, а `bootstrap-test.py` валидирует отсутствие лишних файлов и работоспособность libc++ из апт-кеша хоста.

### Статус по платформам (на 2026-04-15)

| Платформа | Build | Gating test | Publish | Cold bootstrap test | Archive size | Publish run / Test run |
|---|---|---|---|---|---|---|
| linux-x64-gnu | ✓ | ✓ 3/3 libc++ | ✓ ghcr.io/powertech-center/runners/linux-x64-gnu:latest | ✓ 3/3 libc++ + binutils | **2.10 MB** | 24284775999 / 24284884419 |
| linux-arm64-gnu | ✓ | ✓ 3/3 libc++ | ✓ ghcr.io/powertech-center/runners/linux-arm64-gnu:latest | ✓ 3/3 libc++ + binutils | **2.13 MB** | 24284775999 / 24284884419 |
| macos-x64 | ✓ | ✓ | ✓ ghcr.io/powertech-center/runners/macos-x64:latest | — | **66 KB** | 24308867738 / — |
| macos-arm64 | ✓ | ✓ | ✓ ghcr.io/powertech-center/runners/macos-arm64:latest | — | **49.2 MB** | 24308867738 / — |
| windows-x64 | ✓ | ✓ 2/2 libc++ | ✓ ghcr.io/powertech-center/runners/windows-x64:latest | — | **10.9 MB** | 24314368331 / — |
| windows-arm64 | ✓ | ✓ all green | ✓ ghcr.io/powertech-center/runners/windows-arm64:latest | — | **189 MB** (llvm-mingw dominates) | 24399361905 / — |
| linux-x64-musl | ✓ | ✓ 173/173 | ✓ ghcr.io/powertech-center/runners/linux-x64-musl:latest | pending | **108 MB** | 24476528756 / — |
| linux-arm64-musl | ✓ | ✓ 173/173 | ✓ ghcr.io/powertech-center/runners/linux-arm64-musl:latest | pending | **100 MB** | 24476648826 / — |

**Cold bootstrap верифицирован end-to-end для linux-gnu (run 24284884419):**
`bootstrap-test.yml` прогнал `powertech-center/runners/run@feature/debug` на холодных раннерах ubuntu-24.04{,-arm} — композит action скачал `patch.tar.gz` из GHCR, распаковал `sudo tar -xzf -C /`, и `bootstrap-test.py` увидел `lld`/`ld.lld`/`llvm-ar` через PATH (симлинки резолвятся в предустановленные `/usr/bin/lld-18` и т.д. — slim подход работает). 3/3 libc++ теста зелёные на обеих платформах. Runtime: x64 37s, arm64 47s. Это значит, что и gating path (emulate in-place в publish workflow), и consumer path (download+extract в test workflow) используют одинаковую `run/action.yml` логику и оба зелёные. Следующий цикл публикации может спокойно идти через `workflow_dispatch`.

### linux-gnu binutils slim (Вариант A)

Раньше `_install_llvm_binutils_linux_gnu()` копировал в bundle реальные `lld` / `ld.lld` / `llvm-ar` из `/usr/lib/llvm-{N}/bin/` **плюс** 9 runtime библиотек (`libLLVM*.so*`, `libLTO*`, `libRemarks*`) из `/usr/lib/llvm-{N}/lib/`. Именно `libLLVM-{N}.so.{N}.1` занимал почти весь объём — отсюда ~40 MB.

Оптимизация: на ubuntu-24.04 GitHub-раннерах пакеты `lld-{N}` и `llvm-{N}` **уже предустановлены** (подтверждено логом `bootstrap-build`: «apt: all requested packages already installed: lld-18 llvm-18»), т.е. на хосте уже есть `/usr/bin/lld-{N}`, `/usr/bin/ld.lld-{N}`, `/usr/bin/llvm-ar-{N}` и весь LLVM core в `/usr/lib/llvm-{N}/lib/`. Поэтому нет смысла тащить их в bundle.

Вместо этого `_install_llvm_binutils_linux_gnu()` кладёт в ROOT **только три unversioned symlinks**:

```
ROOT/usr/bin/lld      -> /usr/bin/lld-{N}
ROOT/usr/bin/ld.lld   -> /usr/bin/ld.lld-{N}
ROOT/usr/bin/llvm-ar  -> /usr/bin/llvm-ar-{N}
```

`bootstrap-test.py:314-316` ищет unversioned имена через PATH — симлинки это покрывают. Перед созданием симлинков функция делает sanity-check: `fail()` если `/usr/bin/lld-{N}` отсутствует на билд-раннере, чтобы дрифт ubuntu-image был виден сразу и мы пересмотрели стратегию.

**Риск**: привязка к тому, что ubuntu-24.04 image всегда включает `lld-{N}` / `llvm-{N}`. Не больше, чем наша существующая привязка к `clang-{N}` + `libc++-{N}-dev` версии ubuntu, поэтому приемлемо. Install-on-consumer (apt-get в `run/action.yml`) отвергнут: лишние ~10s на каждый запуск ради нескольких тестов в `bootstrap-test.py`.

### Принципы реализации

- **Один файл `bootstrap-build.py` в корне**, без подпапок. Структура: helpers сверху → `build_libcxx()` → функции `build_<platform>()` → dispatcher → CLI.
- **Без манифестов** в bundle (`run/action.yml` их не читает).
- **Локальная отладка не предполагается** — итерируем через CI на раннерах.
- При смене bundle-структуры или CMake-флагов libc++ обновлять CLAUDE.md, чтобы следующая сессия могла продолжить с того же места.

### Soft host cleanup

Для всех платформ кроме musl `bootstrap-build.py` после успешного `build_<platform>()` удаляет с раннера те пакеты, которые **сам** доустановил через apt/brew/choco в процессе сборки ROOT. Это повышает достоверность последующих шагов `Emulate bootstrap` → `Run bootstrap tests` в `bootstrap-build-publish.yml`: тесты проверяют файлы из распакованного bundle, а не случайные остатки apt/brew-установок на раннере.

Механика:
- Единственные точки установки — обёртки `host_install_apt(pkgs)` / `host_install_brew(pkgs)` / `host_install_choco(pkgs)`. Они диффят запрошенное со списком уже установленного на хосте и в трекер `_host_installed` попадают **только реально новые** пакеты.
- `host_cleanup_installed()` вызывается в `main()` после билдера, идёт по трекеру и делает `apt-get remove --purge` / `brew uninstall --ignore-dependencies` / `choco uninstall` с `check=False` (best-effort).
- **Мягкость**: транзитивные зависимости brew/apt не трогаем, пре-существующее состояние раннера не трогаем. Чистка — это дополнительная мера, не гарантия hermetic-окружения.
- **Musl — не чистим**: весь Alpine overlay живёт внутри ROOT, хост apt не используется.

## Findings из диагностики

Данные собраны через (уже удалённый) `audit-toolchain.py` + подтверждены ранними запусками `bootstrap-build-publish.yml`. Оставлены здесь как входные данные для решений.

**Версии компиляторов на раннерах:**

| Platform | clang | GCC | Заметки |
|----------|-------|-----|---------|
| linux-x64-gnu | 18.1.3 (Ubuntu) | 13.3.0 | LLVM 18 из ubuntu repos |
| linux-arm64-gnu | 18.1.3 (Ubuntu) | 13.3.0 | same as x64 |
| linux-x64-musl (host) | 18.1.3 (Ubuntu) | 13.3.0 | host = ubuntu, musl только в ROOT |
| linux-arm64-musl (host) | 18.1.3 (Ubuntu) | 13.3.0 | host = ubuntu |
| macos-x64 | 17.0.0 (Apple) | 17.0.0 | Xcode 26.2 |
| macos-arm64 | 17.0.0 (Apple) | 17.0.0 | Xcode 26.2 |
| windows-x64 | 20.1.8 | 15.2.0 (MinGW) | standalone LLVM 20 |
| windows-arm64 | 20.1.6 | 14.2.0 (MinGW) | standalone LLVM 20 |

**Ключевые выводы и решения:**

1. **Linux gnu**: `clang++ -stdlib=libc++` падает с "iostream file not found" — libc++ headers на раннере отсутствуют. Правильный пакет — versioned `libc++-{major}-dev` (не `libc++-dev`, который конфликтует с `libunwind-dev`). `_libcxx_linux_gnu()` динамически детектит major clang и ставит matching пакеты.
   
   **Layout на ubuntu-24.04** (важный нюанс, выяснено пошагово в runs 24284109807 → 24284303382 → 24284362258):
   - Реальные файлы `libc++.{a,so*}`, `libc++abi.{a,so*}`, **и LLVM** `libunwind.{a,so*}` лежат в `/usr/lib/llvm-{N}/lib/` (пакеты `libc++-{N}-dev`, `libc++abi-{N}-dev`, `libunwind-{N}-dev`).
   - В `/usr/lib/<triplet>/` только **forwarding-симлинки** типа `libc++.so -> ../llvm-18/lib/libc++.so`.
   - В том же `/usr/lib/<triplet>/` параллельно живёт **несвязанный системный libunwind** (`libunwind.so.8*`, `libunwind-x86_64.so.8*`, API 0.99) из пакета `libunwind-dev` — он несовместим с LLVM libunwind и при `clang++ -stdlib=libc++ -lunwind` ломает линковку.
   - Поэтому `_libcxx_linux_gnu()` копирует в ROOT: (a) реальные файлы libc++/libc++abi/libunwind из `llvm-{N}/lib/`, (b) только **поимённый whitelist** forwarding-симлинков в триплет (`libc++.so{,.1,.1.0}`, `libc++abi.so{,.1,.1.0}`, `libunwind.so{,.1,.1.0}`, `.a` варианты). Broad-глоб типа `libunwind*.so*` **нельзя** — он затянет системный libunwind.so.8.

2. **macOS**: libc++ уже в Xcode SDK (`-isystem .../MacOSX.sdk/usr/include/c++/v1` прописан авто). `-stdlib=libc++` динамически работает, **static линк libc++ на Darwin невозможен by design** (линкер отказывается). Bundle для macOS — пустой, `install_libcxx(macos-*)` — no-op, `bootstrap-test.py` не требует static на Darwin.

3. **Windows** (открытый вопрос, итерация 4.5): standalone LLVM 20 таргетит `x86_64-pc-windows-msvc` и берёт MSVC STL. `-stdlib=libc++` выдаёт warning "argument unused" и просто игнорируется — `#include <iostream>` идёт в MSVC STL. Значит класть libc++ в `Program Files/LLVM/include/c++/v1/` недостаточно. Варианты: (a) явные `-nostdinc++` + `-isystem` в bundle; (b) остаться на MSVC STL и не иметь libc++ на Windows; (c) `--target=...-windows-gnu` + MinGW libc++. Решение после обсуждения.

4. **Alpine minirootfs** (подтверждено в run 24281280856 после fix v2 парсера): `latest-releases.yaml` использует `flavor: alpine-minirootfs`, элементы списка разделены строкой с одиноким `-`. Парсер в `_alpine_minirootfs_url()` ищет по state machine, берёт `version` и `branch:` напрямую из YAML. На обоих arch даёт Alpine 3.23.3, branch v3.23, URL вида `https://dl-cdn.alpinelinux.org/alpine/v3.23/releases/{x86_64|aarch64}/alpine-minirootfs-3.23.3-*.tar.gz`.

5. **apk на ubuntu-24.04 noble** (решено): пакета `apk-tools` в noble universe нет. Решение — использовать статический `apk`, который уже лежит в `ROOT/sbin/apk` после распаковки minirootfs. `_apk_install_into_root()` сначала пытается взять его оттуда, потом фолбэк на хостовый. В Alpine 3.23 main-репе есть `libc++ 21.1.2-r0`, `libc++-static`, `libc++-dev`, `llvm-libunwind-static`, `gcc 15.2.0-r2`, `musl-dev 1.2.5-r23` — всё для static libc++ bundle под musl готово из коробки.