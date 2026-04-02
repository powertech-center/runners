# CLAUDE.md

Переиспользуемые GitHub Actions workflows для кросс-платформенного CI на 8 целевых платформах (linux/windows/macos, x64/arm64, gnu/musl).

## Структура

- `.github/workflows/` — по одному reusable workflow на платформу (напр. `linux-x64-gnu.yml`), плюс `test.yml`
- `run/action.yml` — composite action для стандартных платформ (Linux gnu, Windows, macOS)
- `musl/action.yml` — composite action для Alpine/musl платформ (Linux musl)
- `test.ps1` — локальный тестовый скрипт

## Ключевые концепции

- Каждый workflow — тонкая `workflow_call` обёртка, делегирующая в composite action `run/` или `musl/`
- `run/` обслуживает Linux-gnu, Windows, macOS; `musl/` — устаревший код, будет удалён
- Никакие JS actions не используются — весь checkout, артефакты и bootstrap реализованы через shell
- Checkout: shell-based `git fetch --depth=1` + `git checkout FETCH_HEAD`, поддержка submodules и LFS через inputs
- Артефакты управляются через `artifacts-dir`, `artifacts-download`, `artifacts-upload` (REST API)
- Формат триплета платформы: `{os}-{arch}-{libc}` (напр. `linux-x64-gnu`, `windows-x64`, `macos-arm64`)
- Bootstrap (установка инструментов) — планируется

## При редактировании

- Сигнатуры inputs должны быть идентичны во всех 8 workflow
- Изменения тестируются через `test.yml`, который запускает все платформы
- Actions ссылаются как `powertech-center/runners/run@main` или `powertech-center/runners/musl@main`