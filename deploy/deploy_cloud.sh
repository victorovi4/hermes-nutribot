#!/usr/bin/env bash
# Выкладка приложения Нутрибота в Яндекс Облако: функция, шлюз, страница в хранилище, таймер прогрева.
# Настройки — в deploy/cloud.env (пример: deploy/cloud.env.example). Запуск: deploy/deploy_cloud.sh
set -euo pipefail
export YC_CLI_INITIALIZATION_SILENCE=true
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
YC="${YC:-$(command -v yc || echo "$HOME/yandex-cloud/bin/yc")}"
CONFIG="${NUTRI_CLOUD_ENV:-$ROOT/deploy/cloud.env}"
[ -f "$CONFIG" ] || { echo "нет файла настроек $CONFIG — скопируйте deploy/cloud.env.example и заполните"; exit 1; }
set -a; . "$CONFIG"; set +a

FUNCTION="${NUTRI_FUNCTION:-nutribot-api}"
GATEWAY="${NUTRI_GATEWAY:-nutribot}"
TRIGGER="${NUTRI_TIMER:-nutribot-warm}"
SECRET="${NUTRI_LOCKBOX_SECRET:-nutribot-secrets}"
SA_KEY="${NUTRI_GOOGLE_SA_KEY:-$ROOT/secrets/google-sa.key.json}"
case "$SA_KEY" in /*) ;; *) SA_KEY="$ROOT/$SA_KEY" ;; esac
SPREADSHEET_ID="${NUTRI_SPREADSHEET_ID:-}"
ALLOWED="${TELEGRAM_ALLOWED_USERS:-}"
BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
[ -n "$SPREADSHEET_ID" ] || { echo "NUTRI_SPREADSHEET_ID не задан: приложение в Яндекс Облаке работает только с Google-таблицей"; exit 1; }
[ -n "$ALLOWED" ] || { echo "TELEGRAM_ALLOWED_USERS не задан: в приложение никого нельзя будет пустить"; exit 1; }
[ -n "$BOT_TOKEN" ] || { echo "TELEGRAM_BOT_TOKEN не задан: без него не проверить подпись Telegram"; exit 1; }
[ -f "$SA_KEY" ] || { echo "нет ключа служебного Google-аккаунта: $SA_KEY"; exit 1; }
SERVICE_ACCOUNT_ID="$($YC iam service-account get "${NUTRI_YC_SERVICE_ACCOUNT:-hermes-agent}" --format json | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
[ -n "$ALLOWED" ] || { echo "TELEGRAM_ALLOWED_USERS is empty"; exit 1; }

# 1. Secrets. Preferred home is Lockbox (USE_LOCKBOX=1), but the deploying service account needs the
#    lockbox.payloadViewer role for that, and only a cloud admin can grant it. Until then the two secrets
#    travel as environment variables of the function version (visible only inside this cloud folder).
SECRET_ARGS=()
ENV_FILE=""
if [ "${USE_LOCKBOX:-0}" = "1" ]; then
  payload() {
    SA_KEY="$SA_KEY" BOT_TOKEN="$BOT_TOKEN" python3 -c '
import json, os
print(json.dumps([{"key": "GOOGLE_SA_KEY", "text_value": open(os.environ["SA_KEY"]).read()},
                  {"key": "TELEGRAM_BOT_TOKEN", "text_value": os.environ["BOT_TOKEN"]}]))'
  }
  if $YC lockbox secret get "$SECRET" >/dev/null 2>&1; then
    payload | $YC lockbox secret add-version "$SECRET" --payload - >/dev/null
  else
    payload | $YC lockbox secret create --name "$SECRET" --description "Nutribot app: Google service account key and bot token" --payload - >/dev/null
  fi
  SECRET_ID="$($YC lockbox secret get "$SECRET" --format json | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
  SECRET_ARGS=(--secret "environment-variable=GOOGLE_SA_KEY,id=$SECRET_ID,key=GOOGLE_SA_KEY"
               --secret "environment-variable=TELEGRAM_BOT_TOKEN,id=$SECRET_ID,key=TELEGRAM_BOT_TOKEN")
  EXTRA_ENV=""
else
  EXTRA_ENV=",GOOGLE_SA_KEY_B64=$(base64 < "$SA_KEY" | tr -d '\n'),TELEGRAM_BOT_TOKEN=$BOT_TOKEN"
fi

# 2. The page: one self-contained index.html + fonts + a copy of Telegram's script, served from Object Storage,
#    so opening the app never waits for a function cold start.
BUILD="$(mktemp -d)"
trap 'rm -rf "$BUILD"' EXIT
curl -fsS -m 20 -o "$BUILD/telegram-web-app.js" https://telegram.org/js/telegram-web-app.js \
  && [ "$(wc -c < "$BUILD/telegram-web-app.js")" -gt 50000 ] && cp "$BUILD/telegram-web-app.js" "$ROOT/web/telegram-web-app.js" \
  || echo "note: telegram.org is unreachable, keeping the previous copy of telegram-web-app.js"
python3 "$ROOT/deploy/build_web.py" "$BUILD/web" >/dev/null
FOLDER_ID="$($YC config get folder-id)"
BUCKET="${NUTRI_BUCKET:-nutribot-static-${FOLDER_ID: -8}}"
$YC storage bucket get "$BUCKET" >/dev/null 2>&1 || $YC storage bucket create --name "$BUCKET" --default-storage-class standard >/dev/null
put() { $YC storage s3api put-object --bucket "$BUCKET" --key "$1" --body "$BUILD/web/$1" --content-type "$2" --cache-control "$3" >/dev/null; }
put index.html "text/html; charset=utf-8" "no-cache"
put telegram-web-app.js "application/javascript; charset=utf-8" "public, max-age=86400"
for font in "$BUILD"/web/fonts/*.woff2; do put "fonts/$(basename "$font")" "font/woff2" "public, max-age=31536000, immutable"; done

# 3. Function version: the API. Dependencies are built by the platform from requirements.txt.
mkdir -p "$BUILD/src"
cp -R "$ROOT/api" "$ROOT/nutricore" "$BUILD/src/"
cp -R "$BUILD/web" "$BUILD/src/web"
cp "$ROOT/api/requirements.txt" "$BUILD/src/requirements.txt"
find "$BUILD/src" -name __pycache__ -type d -prune -exec rm -rf {} +
(cd "$BUILD/src" && zip -qr ../build.zip .)
$YC serverless function get "$FUNCTION" >/dev/null 2>&1 || $YC serverless function create --name "$FUNCTION" --description "Nutribot Telegram app: page and API" >/dev/null
$YC serverless function version create --function-name "$FUNCTION" --runtime python312 --entrypoint api.handler.handler \
  --memory 256m --concurrency 3 --execution-timeout 30s --source-path "$BUILD/build.zip" --service-account-id "$SERVICE_ACCOUNT_ID" \
  --environment "TELEGRAM_ALLOWED_USERS=$ALLOWED,NUTRI_STORAGE=sheets,NUTRI_SPREADSHEET_ID=$SPREADSHEET_ID$EXTRA_ENV" \
  ${SECRET_ARGS[@]+"${SECRET_ARGS[@]}"} >/dev/null
FUNCTION_ID="$($YC serverless function get "$FUNCTION" --format json | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"

# 4. API gateway: the public HTTPS address. Page files come from the bucket, /api/* goes to the function.
sed -e "s/__FUNCTION_ID__/$FUNCTION_ID/g" -e "s/__SERVICE_ACCOUNT_ID__/$SERVICE_ACCOUNT_ID/g" -e "s/__BUCKET__/$BUCKET/g" "$ROOT/deploy/gateway.yaml" > "$BUILD/gateway.yaml"
if $YC serverless api-gateway get "$GATEWAY" >/dev/null 2>&1; then
  $YC serverless api-gateway update "$GATEWAY" --spec "$BUILD/gateway.yaml" >/dev/null
else
  $YC serverless api-gateway create --name "$GATEWAY" --description "Nutribot Telegram app" --spec "$BUILD/gateway.yaml" >/dev/null
fi
# 5. A timer keeps one function instance warm, so the first request after a pause is not a cold start.
$YC serverless trigger get "$TRIGGER" >/dev/null 2>&1 || $YC serverless trigger create timer --name "$TRIGGER" \
  --description "Keeps the Nutribot API warm" --cron-expression '0/5 * ? * * *' \
  --invoke-function-id "$FUNCTION_ID" --invoke-function-service-account-id "$SERVICE_ACCOUNT_ID" >/dev/null

DOMAIN="$($YC serverless api-gateway get "$GATEWAY" --format json | python3 -c 'import json,sys; print(json.load(sys.stdin)["domain"])')"
echo "https://$DOMAIN/" | tee "${NUTRI_APP_URL_FILE:-$ROOT/deploy/app-url.txt}"
