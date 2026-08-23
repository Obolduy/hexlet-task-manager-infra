#!/usr/bin/env bash
# Локальный прогон review.py на реальном репозитории, без пуша и без
# постинга в GitHub — печатает итоговый комментарий в stdout.
#
# Использование:
#   review-action/local_review.sh <путь-до-репозитория> [base=main] [head=HEAD]
#
# base/head — что угодно, что понимает `git diff base...head`: имя ветки,
# HEAD, SHA. По умолчанию сравнивает текущий чекаут целевого репозитория
# с его веткой main.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ $# -lt 1 ]; then
  echo "использование: $(basename "$0") <путь-до-репозитория> [base=main] [head=HEAD]" >&2
  exit 1
fi

REPO_PATH="$(cd "$1" && pwd)"
BASE="${2:-main}"
HEAD="${3:-HEAD}"

ENV_FILE="$SCRIPT_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
  echo "нет $ENV_FILE — скопируйте .env.example в .env и впишите свой OPENROUTER_API_KEY" >&2
  exit 1
fi

VENV_DIR="$SCRIPT_DIR/.venv"
if [ ! -x "$VENV_DIR/bin/python" ]; then
  echo "venv не найден, создаю ($VENV_DIR)..." >&2
  python3 -m venv "$VENV_DIR"
  "$VENV_DIR/bin/pip" install --quiet --disable-pip-version-check -r "$SCRIPT_DIR/requirements.txt"
fi

set -a
source "$ENV_FILE"
set +a

export REPO_PATH BASE_SHA="$BASE" HEAD_SHA="$HEAD" DRY_RUN=1

echo "репозиторий: $REPO_PATH" >&2
echo "диапазон: $BASE...$HEAD" >&2

exec "$VENV_DIR/bin/python" "$SCRIPT_DIR/review.py"
