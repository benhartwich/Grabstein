# Flüsternder Grabstein – Software

## Verkabelung (physische Pin-Nummern der 40-poligen Leiste)

| Bauteil | Anschluss | Pi-Pin |
|---|---|---|
| Bewegungsmelder HC-SR501 | VCC / OUT / GND | 2 (5 V) / 11 (GPIO17) / 6 |
| Radar LD2410C | VCC / OUT / GND | 4 (5 V) / 15 (GPIO22) / 9 |
| ReSpeaker XVF3800 | USB-C → USB-A am Pi | – |
| Lautsprecher (aktiv) | Klinke → Klinkenbuchse am ReSpeaker, USB → freie USB-Buchse am Pi (nur Strom) | – |

Andere Pins? In `/etc/grabstein/grabstein.env` bei `PIR_PIN` und `RADAR_PIN` die GPIO-Nummer eintragen.

## Installation

1. Ordner `grabstein` per MobaXterm (linke Dateileiste) ins Home-Verzeichnis des Pi ziehen.
2. `bash grabstein/install.sh`
3. `nano /etc/grabstein/grabstein.env` – Agent-ID, API-Key und Voice-ID eintragen (Strg+O speichern, Strg+X beenden).
4. `grabstein clips` – erzeugt die Flüster-Clips aus `clips.json` (eigene MP3s gehen auch: einfach in `/opt/grabstein/clips/<gruppe>/` legen).
5. Testen: `grabstein sensoren`, `grabstein ton`, `grabstein reden`
6. `sudo systemctl start grabstein` – ab jetzt startet der Grabstein bei jedem Einschalten von selbst.

## Was der Grabstein tut

- **Jemand kommt** (Bewegungsmelder): kurzer Flüster-Clip aus `approach`.
- **Jemand bleibt stehen** (Radar ≥ 4 s): Gespräch mit dem ElevenLabs-Agenten, höchstens 90 s.
- **Jemand geht** (Radar 8 s frei): Gespräch endet.
- **Kinder-Modus** bis 19:30 (und immer, solange die Uhrzeit nicht synchronisiert ist): nur liebe Clips aus `gentle`, kein Gespräch.
- **Ohne Internet** oder wenn das Abendbudget (20 min) verbraucht ist: Clips statt Gespräch.
- Außerhalb 16:30–22:30 ist der Grabstein still.

## Nützliche Befehle

| Befehl | Zweck |
|---|---|
| `grabstein status` | Modus, Budget, Clips, Internet |
| `journalctl -u grabstein -f` | Live-Log |
| `sudo systemctl restart grabstein` | nach Änderungen an den Einstellungen |
| `grabstein clips --neu` | Clips neu erzeugen (z. B. nach Stimmenwechsel) |

Den ElevenLabs-Agenten richtest du nach `agent_prompt.md` ein.
