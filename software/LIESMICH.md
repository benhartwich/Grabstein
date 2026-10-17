# Flüsternder Grabstein – Software

## Verkabelung (physische Pin-Nummern der 40-poligen Leiste)

| Bauteil | Anschluss | Pi-Pin |
|---|---|---|
| Bewegungsmelder HC-SR501 | VCC / OUT / GND | 2 (5 V) / 11 (GPIO17) / 6 |
| Radar LD2410C | VCC / OUT / GND | 4 (5 V) / 15 (GPIO22) / 9 |
| Radar LD2410C (Abstand) | RX / TX | 8 (TXD) / 10 (RXD) |
| ReSpeaker XVF3800 | USB-C → USB-A am Pi | – |
| Lautsprecher (passiv, PH2.0-Stecker) | JST-Lautsprecherbuchse am ReSpeaker | – |

Die Zeichnung mit Blickrichtung findest du in `verkabelung.html`. Für andere Pins trägst du in `/etc/grabstein/grabstein.env` bei `PIR_PIN` und `RADAR_PIN` die GPIO-Nummer ein. Ohne RX/TX funktioniert der Grabstein auch, nur ohne Abstand und Lockruf.

## Installation

Getestet mit Raspberry Pi OS Lite 64-bit (Trixie) auf einem Raspberry Pi 4.

1. `git clone https://github.com/benhartwich/Grabstein.git ~/grabstein-repo`
2. `bash ~/grabstein-repo/software/install.sh` installiert Pakete, Python-Umgebung, Tonkonfiguration, die udev-Regel für den LED-Ring und den Dienst.
3. `sudo nano /etc/grabstein/grabstein.env` öffnen und API-Key sowie Voice-ID eintragen (Strg+O speichert, Strg+X beendet).
4. `grabstein agent` legt den ElevenLabs-Agenten „Gustav“ an und trägt seine ID ein.
5. `grabstein clips` erzeugt die Flüster-Clips aus `clips.json`. Eigene MP3s kannst du auch in der Web-Oberfläche hochladen.
6. `sudo bash ~/grabstein-repo/software/pi-schnittstellen.sh && sudo reboot` schaltet den UART für den Radar-Abstand frei. Bluetooth geht dabei aus.
7. Optional für Musik: `sudo bash ~/grabstein-repo/software/musik-einrichten.sh` richtet den Ton-Mischer und Spotify Connect (raspotify) ein.
8. Zum Testen: `grabstein sensoren`, `grabstein ton`, `grabstein reden`.
9. `sudo systemctl start grabstein`. Ab jetzt startet der Grabstein bei jedem Einschalten von selbst.

## Web-Oberfläche

Sie ist im Heimnetz unter **http://grabstein.local** erreichbar. Am Handy kannst du sie mit „Zum Startbildschirm hinzufügen“ ablegen.

- **Start:** Zustand und Betriebsart (Automatik / Aus / Nur Musik / Lieb / Gruselig, auf Wunsch befristet). Dazu Sensoren live mit Radar-Abstand, Clips und Gespräch auslösen, Durchsagen mit Gustavs Stimme und die Zähler des Abends.
- **Zeitplan:** Einträge für Wochentage oder ein bestimmtes Datum, mit Wochenvorschau.
- **Clips:** Texte bearbeiten, neu erzeugen, an- und ausschalten, am Grabstein oder am Handy anhören, eigene MP3s hochladen.
- **Musik:** Quelle wählen (eigene Tracks, Webradio-Stream oder Spotify), Lautstärke und Absenkung während der Clips einstellen, Tracks mit ElevenLabs erzeugen.
- **Gustav:** Gesprächseinstellungen, Codewort fürs gelöste Rätsel, Prompt und erste Nachricht. Alles wird direkt zu ElevenLabs übertragen.
- **Mehr:** Erkennung (Zeiten, Gesprächsabstand), Ton, LED-Ring, Systemzustand, Verlauf, Protokoll, Neustart.

Einstellungen aus der Oberfläche liegen in `/var/lib/grabstein/` und wirken sofort. Optional schützt `WEB_PIN=` in der env-Datei die Oberfläche mit einer PIN.

### Spotify (optional, Premium nötig)

1. Lege auf https://developer.spotify.com/dashboard eine App an. Redirect-URI: `http://127.0.0.1:8888/callback`, API: Web API.
2. Trag die Client-ID als `SPOTIFY_CLIENT_ID=` in die env-Datei ein und starte den Dienst neu. Die Client-ID ist kein Geheimnis.
3. Tippe in der Web-Oberfläche unter Musik → Spotify auf „Mit Spotify verbinden“. Nach der Anmeldung landest du auf einer Fehlerseite unter 127.0.0.1. Das ist normal: Kopiere deren Adresse und füge sie in der Web-Oberfläche ein.
4. Wähle in der Spotify-App einmal das Gerät „Grabstein“ aus. Danach kannst du in der Web-Oberfläche Playlists suchen und auswählen.

## Was der Grabstein tut

- **Jemand kommt** (Bewegungsmelder): Er spielt einen kurzen Flüster-Clip.
- **Jemand bleibt stehen** (Radar ≥ 4 s): Ist die Person weiter weg als der Gesprächsabstand, kommt ein Lockruf. Ist sie nah genug, startet ein Gespräch mit Gustav. Nach 90 s verabschiedet er sich, nach 15 s Stille legt er auf.
- **Jemand geht** (Radar 8 s frei): Das Gespräch endet.
- **Musik** läuft im Hintergrund. Bei Clips wird sie leiser, während eines Gesprächs blendet sie ganz aus.
- **Lieb** ist der Standard bis 19:30 und gilt immer, solange die Uhrzeit nicht synchronisiert ist. Dann kommen nur liebe Clips, und Gespräche nur, wenn sie unter „Gustav“ erlaubt sind.
- **Ohne Internet** oder wenn das Abendbudget (20 min) verbraucht ist, spielt er Clips statt eines Gesprächs.
- Außerhalb des Zeitplans (Standard 16:30–22:30) ist der Grabstein still.

## Nützliche Befehle

| Befehl | Zweck |
|---|---|
| `grabstein status` | Modus, Budget, Clips, Internet |
| `journalctl -u grabstein -f` | Live-Log |
| `sudo systemctl restart grabstein` | nach Änderungen an der env-Datei |
| `grabstein clips --neu` | Clips neu erzeugen (z. B. nach einem Stimmenwechsel) |
| `grabstein agent` | Agent nach Änderungen an `agent_prompt.md` aktualisieren |

## Eigenheit des ReSpeaker XVF3800

Aufnahme und Wiedergabe sind gekoppelt. Das Mikrofon liefert nur Daten, solange gleichzeitig etwas abgespielt wird. Gespräche laufen deshalb über `arecord`/`aplay` mit einem Stille-Strom.
