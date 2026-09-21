#!/bin/bash
# Installa soltanto i servizi; avvio e abilitazione al boot sono comandi separati.
set -euo pipefail

if [[ "$EUID" -ne 0 ]]; then
    echo 'Uso: sudo bash install-services.sh IP_OBELICS_O_PC_SIMULATORE' >&2
    exit 1
fi
if [[ $# -ne 1 ]]; then
    echo 'Indica l’indirizzo IPv4 di ObeliCS oppure del PC che lo simula.' >&2
    exit 1
fi
OBELICS_HOST=$1
python3 - "$OBELICS_HOST" <<'PY'
import ipaddress, sys
try:
    ipaddress.IPv4Address(sys.argv[1])
except ValueError:
    raise SystemExit('Destinazione non valida: usare un indirizzo IPv4, ad esempio 192.168.10.2')
PY
APP_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
if [[ ! "$APP_DIR" =~ ^/[a-zA-Z0-9_./-]+$ ]]; then
    echo 'Usare una cartella senza spazi o caratteri speciali, ad esempio /home/pi/idefix-touch.' >&2
    exit 1
fi
if ! id pi >/dev/null 2>&1; then
    echo 'Questo installer è configurato per l’utente pi, che non risulta presente.' >&2
    exit 1
fi
for executable in cage chromium curl dbus-run-session chvt systemctl runuser; do
    if ! command -v "$executable" >/dev/null; then
        echo "Comando mancante: $executable. Installare prima i pacchetti indicati nel README." >&2
        exit 1
    fi
done
if [[ ! -c /dev/tty7 ]]; then
    echo 'Console /dev/tty7 non disponibile.' >&2
    exit 1
fi
if systemctl is-active --quiet display-manager.service; then
    echo 'È già attivo un display manager. Non installo una seconda sessione grafica: verificare quello esistente.' >&2
    exit 1
fi
for source in idefix_web.py idefix_mavlink.py start-kiosk.sh static/index.html; do
    runuser -u pi -- test -r "$APP_DIR/$source" || {
        echo "File mancante o non leggibile dall’utente pi: $APP_DIR/$source" >&2
        exit 1
    }
done
runuser -u pi -- "$APP_DIR/.venv/bin/python" -c 'import pymavlink' || {
    echo 'Preparare prima .venv e installare requirements.txt nella cartella del pannello.' >&2
    exit 1
}

BACKUP_SUFFIX=$(date +%Y%m%d-%H%M%S)
for target in /etc/systemd/system/idefix-web.service /etc/systemd/system/idefix-kiosk.service /etc/pam.d/idefix-kiosk; do
    if [[ -e "$target" ]]; then
        cp -a -- "$target" "$target.backup-$BACKUP_SUFFIX"
    fi
done

cat > /etc/systemd/system/idefix-web.service <<EOF
[Unit]
Description=Idefix LED web panel
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/.venv/bin/python -u $APP_DIR/idefix_web.py --host $OBELICS_HOST --web-bind 0.0.0.0
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/idefix-kiosk.service <<EOF
[Unit]
Description=Idefix touchscreen kiosk on tty7
Wants=idefix-web.service dbus.socket systemd-logind.service
After=idefix-web.service dbus.socket systemd-logind.service systemd-user-sessions.service plymouth-quit-wait.service getty@tty7.service
Conflicts=getty@tty7.service
ConditionPathExists=/dev/tty0

[Service]
Type=simple
User=pi
WorkingDirectory=$APP_DIR
Environment=XDG_SESSION_TYPE=wayland
Environment=XKB_DEFAULT_LAYOUT=it
PAMName=idefix-kiosk
TTYPath=/dev/tty7
TTYReset=yes
TTYVHangup=yes
TTYVTDisallocate=yes
StandardInput=tty-fail
StandardOutput=journal
StandardError=journal
UtmpIdentifier=tty7
UtmpMode=user
ExecStart=/usr/bin/dbus-run-session -- /bin/sh $APP_DIR/start-kiosk.sh
ExecStartPost=+/usr/bin/chvt 7
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# Sessione locale registrata presso logind, separata da SSH e dalla console tty1.
cat > /etc/pam.d/idefix-kiosk <<'EOF'
auth required pam_unix.so
account required pam_unix.so
session required pam_unix.so
session required pam_loginuid.so
session required pam_systemd.so
EOF
chmod 644 /etc/systemd/system/idefix-web.service /etc/systemd/system/idefix-kiosk.service /etc/pam.d/idefix-kiosk
systemctl daemon-reload
printf 'Servizi installati per pi. Destinazione MAVLink: %s\n' "$OBELICS_HOST"
printf 'Pagina web sulla rete: http://IP_IDEFIX:8080 (hotspot: http://10.42.0.1:8080)\n'
printf 'Per la prova: sudo systemctl restart idefix-web.service idefix-kiosk.service\n'
printf 'Dopo la prova riuscita: sudo systemctl enable idefix-web.service idefix-kiosk.service\n'
