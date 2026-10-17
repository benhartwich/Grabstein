#!/usr/bin/env bash
# Installiert den Flüsternden Grabstein auf Raspberry Pi OS Lite (64-bit).
# Aufruf als normaler Benutzer:  bash grabstein/install.sh
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
DEST=/opt/grabstein
ETC=/etc/grabstein
ME="$(id -un)"

say() { printf '\n\033[1;35m== %s\033[0m\n' "$*"; }

if [ "$(id -u)" -eq 0 ]; then
  echo "Bitte als normaler Benutzer starten (nicht mit sudo)."; exit 1
fi

say "1/6 Pakete installieren (dauert ein paar Minuten)"
sudo apt-get update -qq
sudo apt-get install -y -qq python3-venv python3-pip python3-gpiozero python3-pyaudio \
  mpg123 alsa-utils >/dev/null
sudo apt-get install -y -qq python3-lgpio >/dev/null 2>&1 || echo "(python3-lgpio nicht verfügbar – gpiozero nutzt einen anderen Treiber)"

say "2/6 Programm nach $DEST kopieren"
sudo mkdir -p "$DEST" "$ETC"
sudo cp "$SRC/grabstein.py" "$SRC/web.py" "$SRC/clips.json" "$SRC/agent_prompt.md" "$DEST/"
sudo rm -rf "$DEST/web" && sudo cp -r "$SRC/web" "$DEST/web"
[ -d "$SRC/clips" ] && sudo cp -r "$SRC/clips" "$DEST/"
sudo mkdir -p "$DEST/clips"/{approach,stay,leave,gentle}
sudo chown -R "$ME:$ME" "$DEST"
# Einstellungen, Zeitplan, Clip-Texte und Zähler aus der Web-Oberfläche
sudo mkdir -p /var/lib/grabstein && sudo chown "$ME:$ME" /var/lib/grabstein

say "3/6 Python-Umgebung mit ElevenLabs-SDK"
python3 -m venv --system-site-packages "$DEST/venv"
"$DEST/venv/bin/pip" install -q --upgrade pip
"$DEST/venv/bin/pip" install -q "elevenlabs==2.73.0" "pyusb==1.3.1" "pyserial==3.5"
"$DEST/venv/bin/python" -c "import gpiozero, pyaudio, elevenlabs; print('Python-Module ok')"

say "4/6 ReSpeaker als Standard-Audiogerät einrichten"
CARD="$(awk -F'[][]' '/reSpeaker|XVF3800/ {gsub(/ /,"",$2); print $2; exit}' /proc/asound/cards || true)"
if [ -z "$CARD" ]; then
  echo "!! ReSpeaker nicht gefunden. Ist er per USB angesteckt? Danach install.sh einfach nochmal starten."
else
  echo "ReSpeaker gefunden: Karte '$CARD'"
  # Variante A: Mikrofon nur Kanal 0 (aufbereitetes Signal mit Echounterdrückung)
  sudo tee /etc/asound.conf >/dev/null <<EOF
pcm.!default {
  type asym
  playback.pcm "grabstein_out"
  capture.pcm "grabstein_in"
}
pcm.grabstein_out { type plug; slave.pcm "hw:$CARD,0" }
pcm.grabstein_in { type plug; slave.pcm "grabstein_route" }
pcm.grabstein_route {
  type route
  slave { pcm "hw:$CARD,0"; channels 2 }
  ttable.0.0 1
}
ctl.!default { type hw; card $CARD }
EOF
  if ! arecord -q -D default -f S16_LE -r 16000 -c 1 -d 1 /tmp/grabstein-probe.wav 2>/dev/null; then
    echo "Kanalwahl klappt nicht – nehme einfache Variante"
    sudo tee /etc/asound.conf >/dev/null <<EOF
pcm.!default { type plug; slave.pcm "hw:$CARD,0" }
ctl.!default { type hw; card $CARD }
EOF
    arecord -q -D default -f S16_LE -r 16000 -c 1 -d 1 /tmp/grabstein-probe.wav \
      && echo "Mikrofon ok" || echo "!! Mikrofon-Test fehlgeschlagen – bitte Ausgabe an Claude schicken"
  else
    echo "Mikrofon ok"
  fi
fi

say "5/6 Einstellungen"
if [ ! -f "$ETC/grabstein.env" ]; then
  sudo cp "$SRC/grabstein.env.example" "$ETC/grabstein.env"
fi
sudo chown "$ME:$ME" "$ETC/grabstein.env"
sudo chmod 600 "$ETC/grabstein.env"
sudo usermod -aG gpio,audio "$ME" || true
# LED-Ring des ReSpeakers per USB steuern (Gruppe audio)
sudo cp "$SRC/60-respeaker.rules" /etc/udev/rules.d/
sudo udevadm control --reload && sudo udevadm trigger --attr-match=idVendor=2886 || true
# Dienst steuern und Herunterfahren aus der Web-Oberfläche ohne Passwort – nur diese Befehle
echo "$ME ALL=(root) NOPASSWD: /usr/bin/systemctl restart grabstein, /usr/bin/systemctl stop grabstein, /usr/bin/systemctl start grabstein, /usr/bin/systemctl poweroff" \
  | sudo tee /etc/sudoers.d/grabstein >/dev/null
sudo chmod 440 /etc/sudoers.d/grabstein

sudo tee /usr/local/bin/grabstein >/dev/null <<'EOF'
#!/usr/bin/env bash
# Startet Testbefehle; hält dafür den Dienst kurz an (GPIO und Audio sind sonst belegt)
# und startet ihn danach wieder.
set -a; . /etc/grabstein/grabstein.env; set +a
PY=(/opt/grabstein/venv/bin/python /opt/grabstein/grabstein.py "$@")
case "${1:-run}" in run|status|agent) exec "${PY[@]}" ;; esac
if systemctl is-active --quiet grabstein; then
  echo "Dienst wird für den Test angehalten und danach wieder gestartet"
  sudo systemctl stop grabstein
  trap 'sudo systemctl start grabstein' EXIT
fi
"${PY[@]}"
EOF
sudo chmod 755 /usr/local/bin/grabstein

say "6/6 Autostart einrichten"
sudo tee /etc/systemd/system/grabstein.service >/dev/null <<EOF
[Unit]
Description=Fluesternder Grabstein
After=network-online.target sound.target
Wants=network-online.target

[Service]
User=$ME
Group=$ME
SupplementaryGroups=gpio audio
EnvironmentFile=$ETC/grabstein.env
# lgpio legt Hilfsdateien im Arbeitsverzeichnis an; / ist nicht beschreibbar
RuntimeDirectory=grabstein
WorkingDirectory=/run/grabstein
Environment=LG_WD=/run/grabstein
StateDirectory=grabstein
# Web-Oberfläche auf Port 80
AmbientCapabilities=CAP_NET_BIND_SERVICE
ExecStart=$DEST/venv/bin/python $DEST/grabstein.py run
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable grabstein >/dev/null

say "Fertig!"
cat <<EOF
Nächste Schritte:
  1. Schlüssel eintragen:     nano $ETC/grabstein.env
  2. Clips erzeugen:          grabstein clips
  3. Sensoren testen:         grabstein sensoren
  4. Ton und Mikro testen:    grabstein ton
  5. Gespräch testen:         grabstein reden
  6. Dienst starten:          sudo systemctl start grabstein
     Web-Oberfläche:          http://$(hostname).local
     Log ansehen:             journalctl -u grabstein -f
EOF
