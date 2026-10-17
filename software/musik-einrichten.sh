#!/usr/bin/env bash
# Richtet die Musik ein: Ton-Mischer (dmix) systemweit und Spotify Connect (raspotify).
#   sudo bash ~/grabstein-repo/software/musik-einrichten.sh
#
# - /etc/asound.conf: alle Programme spielen über einen gemeinsamen Mischer auf den ReSpeaker;
#   das Gerät „musik“ hat einen eigenen Regler „Musik“, über den der Grabstein ein- und ausblendet.
# - raspotify (librespot) macht den Pi zum Spotify-Lautsprecher „Grabstein“ (Premium nötig) und
#   spielt auf „musik“. Paketquelle: https://dtcooper.github.io/raspotify
set -euo pipefail
if [ "$(id -u)" -ne 0 ]; then echo "Bitte mit sudo starten."; exit 1; fi
SRC="$(cd "$(dirname "$0")" && pwd)"

CARD="$(awk -F'[][]' '/reSpeaker|XVF3800/ {gsub(/ /,"",$2); print $2; exit}' /proc/asound/cards || true)"
if [ -z "$CARD" ]; then echo "ReSpeaker nicht gefunden – USB prüfen."; exit 1; fi

echo "== Ton-Mischer für Karte $CARD"
[ -f /etc/asound.conf ] && cp /etc/asound.conf /etc/asound.conf.vor-musik
sed "s/@CARD@/$CARD/g" "$SRC/asound.conf.vorlage" > /etc/asound.conf

echo "== Spotify Connect (raspotify)"
if ! command -v librespot >/dev/null; then
  apt-get install -y -qq curl gpg >/dev/null
  curl -fsSL https://dtcooper.github.io/raspotify/key.asc | gpg --dearmor -o /usr/share/keyrings/raspotify.gpg --yes
  echo "deb [signed-by=/usr/share/keyrings/raspotify.gpg] https://dtcooper.github.io/raspotify raspotify main" \
    > /etc/apt/sources.list.d/raspotify.list
  apt-get update -qq
  apt-get install -y -qq raspotify
fi
CONF=/etc/raspotify/conf
set_conf() { sed -i "/^#\?$1=/d" "$CONF"; echo "$1=$2" >> "$CONF"; }
set_conf LIBRESPOT_NAME '"Grabstein"'
set_conf LIBRESPOT_BACKEND alsa
set_conf LIBRESPOT_DEVICE musik
set_conf LIBRESPOT_BITRATE 160
set_conf LIBRESPOT_INITIAL_VOLUME 100
set_conf LIBRESPOT_VOLUME_CTRL fixed
systemctl enable raspotify >/dev/null 2>&1
systemctl restart raspotify

# Die Test-Konfiguration im Home-Verzeichnis ist jetzt überflüssig
for h in /home/*; do [ -f "$h/.asoundrc" ] && grep -q "grabstein_mix" "$h/.asoundrc" && rm -f "$h/.asoundrc"; done
systemctl restart grabstein
echo "Fertig. In Spotify erscheint jetzt das Gerät „Grabstein“."
