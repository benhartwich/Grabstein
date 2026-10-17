#!/usr/bin/env bash
# Schaltet die Schnittstellen für Radar-Abstand und LED-Streifen frei. Danach neu starten.
#   sudo bash ~/grabstein-repo/software/pi-schnittstellen.sh && sudo reboot
#
# - UART an Pin 8/10 für das Radar (LD2410C sendet mit 256000 Baud):
#   Konsole von der seriellen Schnittstelle nehmen, UART einschalten und Bluetooth abschalten,
#   damit der genaue PL011-UART auf Pin 8/10 liegt (Bluetooth braucht der Grabstein nicht).
# - SPI an Pin 19 für den WS2812-Streifen; beim Pi 4 braucht SPI dafür einen festen Kerntakt.
set -euo pipefail
if [ "$(id -u)" -ne 0 ]; then echo "Bitte mit sudo starten."; exit 1; fi

CONFIG=/boot/firmware/config.txt
raspi-config nonint do_serial_cons 1   # keine Login-Konsole auf der seriellen Schnittstelle
raspi-config nonint do_serial_hw 0     # UART an
raspi-config nonint do_spi 0           # SPI an

add() { grep -qxF "$1" "$CONFIG" || echo "$1" >> "$CONFIG"; }
grep -qx "\[all\]" "$CONFIG" || echo "[all]" >> "$CONFIG"
add "dtoverlay=disable-bt"
add "core_freq=500"
add "core_freq_min=500"
systemctl disable --now hciuart 2>/dev/null || true

echo "Fertig. Jetzt neu starten: sudo reboot"
