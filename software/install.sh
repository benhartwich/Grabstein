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
sudo cp "$SRC/grabstein.py" "$SRC/clips.json" "$SRC/agent_prompt.md" "$DEST/"
[ -d "$SRC/clips" ] && sudo cp -r "$SRC/clips" "$DEST/"
sudo mkdir -p "$DEST/clips"/{approach,stay,leave,gentle}
sudo chown -R "$ME:$ME" "$DEST"

say "3/6 Python-Umgebung mit ElevenLabs-SDK"
python3 -m venv --system-site-packages "$DEST/venv"
"$DEST/venv/bin/pip" install -q --upgrade pip
"$DEST/venv/bin/pip" install -q "elevenlabs==2.73.0"
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

sudo tee /usr/local/bin/grabstein >/dev/null <<'EOF'
#!/usr/bin/env bash
# Startet Testbefehle; hält dafür den Dienst kurz an (GPIO und Audio sind sonst belegt).
if [ "${1:-run}" != "run" ] && [ "${1:-}" != "status" ] && systemctl is-active --quiet grabstein; then
  echo "Dienst wird für den Test angehalten (danach: sudo systemctl start grabstein)"
  sudo systemctl stop grabstein
fi
set -a; . /etc/grabstein/grabstein.env; set +a
exec /opt/grabstein/venv/bin/python /opt/grabstein/grabstein.py "$@"
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
     Log ansehen:             journalctl -u grabstein -f
EOF
