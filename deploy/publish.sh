#!/usr/bin/env bash
# Публикация в открытый репозиторий: копирует только то, что отслеживается git, без личного (local/, secrets, .env).
# Личная история разработки остаётся в рабочем репозитории; наружу уходит чистое дерево.
# Использование: deploy/publish.sh "Сообщение релиза"   (по умолчанию — дата)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REMOTE="${NUTRI_PUBLIC_REMOTE:-git@github.com:victorovi4/hermes-nutribot.git}"
WORK="${NUTRI_PUBLIC_CLONE:-$HOME/.cache/hermes-nutribot-public}"
MESSAGE="${1:-Обновление $(date +%d.%m.%Y)}"

git -C "$ROOT" diff --quiet || { echo "есть несохранённые изменения — сначала git commit"; exit 1; }
[ -d "$WORK/.git" ] || git clone "$REMOTE" "$WORK"
git -C "$WORK" fetch --quiet origin && git -C "$WORK" reset --quiet --hard origin/HEAD 2>/dev/null || true

# Переносим ровно отслеживаемые файлы: ничего лишнего попасть не может.
find "$WORK" -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
git -C "$ROOT" ls-files -z | grep -zv '^local/' | while IFS= read -r -d '' file; do
  mkdir -p "$WORK/$(dirname "$file")"
  cp -p "$ROOT/$file" "$WORK/$file"
done

# Последняя проверка: ничего личного в том, что уходит наружу.
if grep -rIn -E "1[A-Za-z0-9_-]{25,}|[a-z0-9-]+\.apigw\.yandexcloud\.net|@[a-z0-9-]+\.iam\.gserviceaccount\.com|BEGIN [A-Z ]*PRIVATE KEY|sk-[A-Za-z0-9]{20}|[0-9]{6,}:[A-Za-z0-9_-]{30,}" "$WORK" --exclude-dir=.git --exclude=publish.sh; then
  echo "НАЙДЕНО ЛИЧНОЕ — публикация остановлена"; exit 1
fi

git -C "$WORK" add -A
git -C "$WORK" diff --cached --quiet && { echo "публиковать нечего"; exit 0; }
git -C "$WORK" commit -q -m "$MESSAGE"
git -C "$WORK" push -q origin HEAD
echo "опубликовано: $REMOTE"
