# Flüsternder Grabstein – Kontext für Claude Code

3D-gedruckter Halloween-Grabstein mit Raspberry Pi 4. Bewegungsmelder → Flüster-Clip, Radar „bleibt stehen“ → Live-Gespräch mit dem ElevenLabs-Agenten „Gustav“. Persönliche Notizen und offene Aufgaben stehen in `CLAUDE.local.md` (nicht im Repo).

## Regeln

- **Geheimnisse:** `/etc/grabstein/grabstein.env` enthält den ElevenLabs-API-Key. Die Datei nie komplett ausgeben und nie committen. Zum Nachsehen: `sudo grep -v -E 'KEY|TOKEN' /etc/grabstein/grabstein.env`. Spotify-Tokens liegen in `/var/lib/grabstein/spotify.json`.
- Vor Änderungen an systemd, ALSA oder Paketen kurz sagen, was passiert.
- Das Repo ist öffentlich: keine Schlüssel, IDs, Adressen oder Fotos mit Standortdaten committen.

## Hardware und Verkabelung

- Raspberry Pi 4, Raspberry Pi OS Lite 64-bit (Trixie), Hostname `grabstein`, User `ben`
- Bewegungsmelder HC-SR501: VCC Pin 2, OUT Pin 11 (GPIO17), GND Pin 6
- Radar HLK-LD2410C: VCC Pin 4, OUT Pin 15 (GPIO22), GND Pin 9, RX → Pin 8, TX → Pin 10 (UART /dev/serial0, 256000 Baud, Abstand + Stärke ~10×/s; Bluetooth dafür aus)
- ReSpeaker XVF3800 (USB, ohne XIAO): USB-C an USB-A des Pi, ALSA-Karte `Array`
- Lautsprecher: **passiv**, Kleinlautsprecher mit PH2.0-Stecker an der JST-Lautsprecherbuchse des ReSpeakers (Verstärker an Bord). Kein eigener Strom, keine Klinke. Klinke am ReSpeaker nur für Kopfhörer-Tests, Klinke am Pi nie benutzen.
- Zeichnung: `software/verkabelung.html`

## Wo was liegt

| Pfad | Inhalt |
|---|---|
| `~/grabstein-repo` | dieses Repo (Quelle der Wahrheit) |
| `software/` | `grabstein.py` (Kern + CLI), `web.py` (HTTP-API, nur stdlib), `web/` (Oberfläche, Vanilla-JS), `install.sh`, `clips.json`/`agent_prompt.md` (nur Vorlagen für den ersten Start), `grabstein.env.example` |
| `gehaeuse/` | CAD (Python/manifold3d), STL, Orca-Projekte – auf dem Pi nur Ablage |
| `/opt/grabstein` | installierte Software + venv (`elevenlabs==2.73.0`), Clips in `/opt/grabstein/clips/` |
| `/etc/grabstein/grabstein.env` | nur Schlüssel, Agent/Voice-ID, Modelle, Pins, `WEB_PORT`, `WEB_PIN` (chmod 600) |
| `/var/lib/grabstein/` | `settings.json` (Zeitplan, Betriebsart, Zeiten, Lautstärke – über das Web änderbar, wirkt sofort), `clips.json` (Clip-Texte/Schalter), `gustav.json` (Prompt), `state.json` (Zähler/Budget pro Abend) |
| `/usr/local/bin/grabstein` | Wrapper; hält für Testbefehle den Dienst an |
| `grabstein.service` | systemd-Dienst (User ben, Gruppen gpio audio) |
| `/etc/asound.conf` | ReSpeaker als Standardgerät, Aufnahme Kanal 0 |

Web-Oberfläche: **http://grabstein.local** (Port 80, im Dienst als Thread). Befehle: `grabstein run | sensoren | ton | reden | clips [--neu] | status | agent` – der Wrapper hält den Dienst für Tests an und startet ihn danach wieder. Log: `journalctl -u grabstein -f`.

**Audio-Eigenheit ReSpeaker:** Aufnahme und Wiedergabe sind gekoppelt – das Mikrofon liefert nur, solange gleichzeitig abgespielt wird. Gespräche laufen deshalb über `arecord`/`aplay` mit Stille-Strom (PortAudio hing außerdem beim Stoppen). Für UI-Tests ist `chromium` installiert (headless Screenshots / DevTools).

Modelle: Clips `eleven_v4`, Agent `eleven_v4_turbo` (beides bestätigt: Stimme passt).

## Verhalten (Kurzfassung)

Hauptschleife alle 0,1 s; Web-Befehle laufen über eine Queue in die Schleife. Laufende Clips werden nie unterbrochen, Auslösungen währenddessen verfallen. Modus kommt aus Betriebsart (Automatik/Aus/Nur Musik/Lieb/Gruselig, optional befristet; „Nur Musik“ = Sensoren ruhen) bzw. Zeitplan (Wochentags- und Datums-Einträge; Datum ersetzt an dem Tag die Wochentage; bei Überschneidung gewinnt aus > gruselig > lieb > nur Musik). Ohne Zeitsync im Automatik-Betrieb nur „lieb“. Standard: Clip-Pause 45 s, Gesprächs-Pause 120 s, Gespräch max. 90 s, Ende nach 8 s Radar frei, Budget 20 min/Abend, 16:30–19:30 lieb, 19:30–22:30 gruselig. Von Hand gestartete Gespräche ignorieren das Radar.
