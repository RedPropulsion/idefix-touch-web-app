#!/bin/sh
# Avviare da una console locale o da una sessione grafica configurata, non da SSH.
set -eu
if [ "$(id -u)" -eq 0 ]; then
    echo 'Avvia il browser come utente normale, non con sudo.' >&2
    exit 1
fi
if [ -z "${XDG_RUNTIME_DIR:-}" ]; then
    echo 'Manca una sessione grafica/console locale. Una normale sessione SSH non basta.' >&2
    exit 1
fi
# Attende il servizio locale prima di aprire il browser.
while ! curl --fail --silent --max-time 2 http://127.0.0.1:8080/api/info >/dev/null; do
    sleep 1
done
exec cage -s -- chromium \
    --ozone-platform=wayland \
    --kiosk --no-first-run --noerrdialogs \
    --user-data-dir="$HOME/.local/share/idefix-kiosk" \
    http://127.0.0.1:8080
