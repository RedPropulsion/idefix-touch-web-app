#!/bin/sh
# Diagnostica di sola lettura. Eseguire su Idefix, anche via SSH.
printf '\nSistema e architettura\n'
cat /etc/os-release
uname -r
uname -m
id
printf '\nConnettori grafici\n'
ls -l /dev/dri 2>/dev/null || true
for connector in /sys/class/drm/card*-*/status; do
    [ -f "$connector" ] || continue
    printf '%s: ' "$connector"
    cat "$connector"
done
printf '\nDispositivi di input\n'
cat /proc/bus/input/devices
printf '\nConfigurazione display nel boot\n'
for boot_config in /boot/firmware/config.txt /boot/config.txt; do
    [ -f "$boot_config" ] || continue
    printf '%s\n' "$boot_config"
    grep -E '^\[|^[[:space:]]*(dtoverlay|dtparam|display_|disable_fw_kms_setup|max_framebuffers|lcd_|hdmi_)' "$boot_config" || true
done
printf '\nPacchetti grafici installati\n'
dpkg-query -W -f='${binary:Package}: ${Status}\n' cage chromium labwc lightdm greetd libpam-systemd 2>/dev/null || true
printf '\nGestore grafico\n'
systemctl get-default
systemctl is-active display-manager.service || true
