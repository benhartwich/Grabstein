# Flüsternder Grabstein – Kontext für Claude Code

Halloween-Deko von Benjamin: 3D-gedruckter Grabstein (Snapmaker U1) mit Raspberry Pi 4. Bewegungsmelder → Flüster-Clip, Radar „bleibt stehen“ → Live-Gespräch mit dem ElevenLabs-Agenten „Gustav“. Halloween ist am 31.10.

## Arbeitsweise

- Benjamin arbeitet am Handy (Termius, tmux). Antworten kurz halten, auf Deutsch. Befehle selbst ausführen statt sie zum Abtippen zu schicken.
- Er will nichts manuell machen müssen, schätzt aber ehrliche Widerworte mehr als Zustimmung.
- **Geheimnisse:** `/etc/grabstein/grabstein.env` enthält den ElevenLabs-API-Key. Die Datei nie komplett ausgeben und nie committen. Zum Nachsehen: `sudo grep -v -E 'KEY|TOKEN' /etc/grabstein/grabstein.env`.
- Vor Änderungen an systemd, ALSA oder Paketen kurz sagen, was passiert.
- Myboxi (anderes Projekt) ist geparkt, nicht anfassen.

## Hardware und Verkabelung

- Raspberry Pi 4, Raspberry Pi OS Lite 64-bit (Trixie), Hostname `grabstein`, User `ben`
- Bewegungsmelder HC-SR501: VCC Pin 2, OUT Pin 11 (GPIO17), GND Pin 6
- Radar HLK-LD2410C: VCC Pin 4, OUT Pin 15 (GPIO22), GND Pin 9, TX/RX frei
- ReSpeaker XVF3800 (USB, ohne XIAO): USB-C an USB-A des Pi, ALSA-Karte `Array`
- Lautsprecher: **passiv**, Kleinlautsprecher mit PH2.0-Stecker an der JST-Lautsprecherbuchse des ReSpeakers (Verstärker an Bord). Kein eigener Strom, keine Klinke. Klinke am ReSpeaker nur für Kopfhörer-Tests, Klinke am Pi nie benutzen.
- Zeichnung: `software/verkabelung.html`

## Wo was liegt

| Pfad | Inhalt |
|---|---|
| `~/grabstein-repo` | dieses Repo (Quelle der Wahrheit) |
| `software/` | `grabstein.py`, `install.sh`, `clips.json`, `agent_prompt.md`, `grabstein.env.example` |
| `gehaeuse/` | CAD (Python/manifold3d), STL, Orca-Projekte – auf dem Pi nur Ablage |
| `/opt/grabstein` | installierte Software + venv (`elevenlabs==2.73.0`), Clips in `/opt/grabstein/clips/` |
| `/etc/grabstein/grabstein.env` | Einstellungen und Schlüssel (chmod 600) |
| `/usr/local/bin/grabstein` | Wrapper; hält für Testbefehle den Dienst an |
| `grabstein.service` | systemd-Dienst (User ben, Gruppen gpio audio) |
| `/etc/asound.conf` | ReSpeaker als Standardgerät, Aufnahme Kanal 0 |

Befehle: `grabstein run | sensoren | ton | reden | clips [--neu] | status | agent`, Log: `journalctl -u grabstein -f`.

Modelle: Clips `eleven_v4`, Agent `eleven_v4_turbo` (beides bestätigt: Stimme passt).

## Verhalten (Kurzfassung)

Hauptschleife alle 0,1 s. Laufende Clips werden nie unterbrochen, Auslösungen währenddessen verfallen. Clip-Pause 45 s, Gesprächs-Pause 120 s, Gespräch max. 90 s, Ende nach 8 s Radar frei, Budget 20 min/Abend, aktiv 16:30–22:30, bis 19:30 und ohne Zeitsync nur „gentle“-Clips.

## Offene Aufgaben (der Reihe nach)

1. **GitHub:** Leeres privates Repo `benhartwich/grabstein` existiert schon. `gh` installieren (`sudo apt install -y gh`), `gh auth login` (Device-Login – Benjamin bestätigt den Code am Handy), dann `git remote add origin` und `git push -u origin main`.
2. **Neue `software/grabstein.py` ausrollen:** Sie lädt das SDK beim Dienststart vor und loggt „Erste Antwort nach X s“. `sudo cp software/grabstein.py /opt/grabstein/ && sudo systemctl restart grabstein`, dann `grabstein reden` testen. Vorher war die Wartezeit ca. 3 s (Python-/SDK-Import, Signed-URL, Websocket, erste TTS).
3. **Lautsprecher an der JST-Buchse testen:** `grabstein ton`. Kopfhörer an der ReSpeaker-Klinke gingen schon. Bleibt der Lautsprecher stumm: Seeed listet einen Verstärker-Enable (XVF3800 GPIO X0D31, low = an) – Seeed-Wiki und `xvf_host`-Tool recherchieren und das Einschalten ins Programm bzw. `install.sh` einbauen.
4. **Lautstärke einpendeln:** `alsamixer -c Array` bzw. `amixer -c Array scontrols`; Ergebnis als `LAUTSTAERKE=` in die env-Datei. Kleine Lautsprecher klirren ab ~85 %.
5. **Optional Latenz weiter senken:** Signed-URL vorab holen und kurz cachen.

Nach jeder Änderung: committen und pushen.
