# Flüsternder Grabstein

Ein 3D-gedruckter Halloween-Grabstein für den Vorgarten. Läuft jemand vorbei, flüstert er. Bleibt jemand stehen, unterhält sich Gustav live mit den Besuchern. Gustav ist ein alter, freundlich-gruseliger Geist und läuft als ElevenLabs-Konversationsagent. Bis jemand kommt, spielt leise Gruselmusik. Sie blendet aus, sobald Gustav spricht, und danach wieder ein.

![Vorschau](gehaeuse/vorschau_front.png)

## Was er kann

- **Flüstern beim Vorbeigehen.** Der Bewegungsmelder löst kurze Clips aus, zum Beispiel „Psst … komm näher …“.
- **Lockruf.** Wer stehen bleibt, aber noch weit weg ist (Radar-Abstand), wird näher gerufen.
- **Live-Gespräch.** Wer nah stehen bleibt, kann mit Gustav sprechen. Gustav fragt, als was man verkleidet ist, stellt ein Rätsel, löst es auf und verabschiedet sich nach etwa 90 Sekunden von selbst. Eine optionale Belohnung ist ein Codewort, zum Beispiel für Süßigkeiten an der Tür.
- **Lieb und gruselig.** Am frühen Abend kommen nur kinderfreundliche Clips, später wird es gruseliger.
- **Hintergrundmusik** aus eigenen MP3s, einem Webradio-Stream oder einer Spotify-Playlist (Premium). Die MP3s kann man auch mit ElevenLabs erzeugen lassen.
- **Web-Oberfläche** fürs Handy unter `http://grabstein.local`. Dort lassen sich Zeitplan, Betriebsart, Clips, Gustavs Prompt, Musik, Lautstärke und Erkennung einstellen. Dazu gibt es Live-Sensoren und ein Protokoll.
- **Kosten unter Kontrolle.** Das Gesprächsbudget pro Abend ist begrenzt. Ist es aufgebraucht oder fehlt das Internet, spielt er Clips statt eines Gesprächs.

## Hardware

| Teil | Aufgabe |
|---|---|
| Raspberry Pi 4 (2 GB reichen) | Steuerung, Raspberry Pi OS Lite 64-bit |
| Seeed ReSpeaker XVF3800 (USB, ohne XIAO) | Mikrofon-Array mit Echounterdrückung, Verstärker, LED-Ring |
| Passiver Kleinlautsprecher mit PH2.0-Stecker | an der JST-Lautsprecherbuchse des ReSpeakers |
| HC-SR501 | Bewegungsmelder: „jemand kommt“ |
| HLK-LD2410C | Radar: „jemand bleibt stehen“, mit Abstand |
| 8 Dupont-Kabel (Buchse–Buchse), USB-C-Netzteil 5 V / 3 A | – |
| Schrauben M3 / M2,5, Heißkleber, optional Lautsprecherstoff | siehe [Gehäuse](gehaeuse/LIESMICH.md) |

## Verkabelung

Pin-Nummern sind die physischen Nummern der 40-poligen Leiste:

| Bauteil | VCC | OUT | GND | Daten |
|---|---|---|---|---|
| Bewegungsmelder HC-SR501 | Pin 2 (5 V) | Pin 11 (GPIO17) | Pin 6 | – |
| Radar LD2410C | Pin 4 (5 V) | Pin 15 (GPIO22) | Pin 9 | RX → Pin 8, TX → Pin 10 |

Der ReSpeaker hängt per USB am Pi. Der Lautsprecher steckt am ReSpeaker und bekommt dort Ton und Strom. Die Klinke am Pi bleibt frei. Bilder mit Blickrichtung stehen in [`software/verkabelung.html`](software/verkabelung.html) (im Browser öffnen), die Fotos in [`docs/`](docs/).

## Drucken

Fertige Druckprojekte für Snapmaker Orca liegen in [`gehaeuse/druck/`](gehaeuse/druck/), die STL-Dateien in [`gehaeuse/stl/`](gehaeuse/stl/). Die Frontplatte ist vierfarbig, alles andere einfarbig PLA. Das CAD ist parametrisch in Python geschrieben. Druck, Schrauben und Montage stehen in [`gehaeuse/LIESMICH.md`](gehaeuse/LIESMICH.md).

## Installation

Voraussetzung ist ein ElevenLabs-Konto mit API-Key und einer Stimme aus der Voice Library.

```bash
git clone https://github.com/benhartwich/Grabstein.git ~/grabstein-repo
bash ~/grabstein-repo/software/install.sh
sudo nano /etc/grabstein/grabstein.env      # API-Key und Voice-ID eintragen
grabstein agent                             # legt den Agenten „Gustav“ per API an
grabstein clips                             # erzeugt die Flüster-Clips
sudo bash ~/grabstein-repo/software/pi-schnittstellen.sh && sudo reboot   # UART für den Radar-Abstand
sudo bash ~/grabstein-repo/software/musik-einrichten.sh                   # optional: Mischer + Spotify
```

Danach öffnest du `http://grabstein.local` am Handy. Details stehen in [`software/LIESMICH.md`](software/LIESMICH.md).

## Datenschutz und Recht

Der Grabstein hört Menschen zu, die an deinem Grundstück vorbeigehen, und schickt ihre Stimme während des Gesprächs an ElevenLabs. Bitte beachte:

- **Stell ein Hinweisschild auf**, zum Beispiel „Sprechender Grabstein. Gespräche werden live von einer KI beantwortet und nicht gespeichert.“
- `grabstein agent` schaltet das Speichern von Audio bei ElevenLabs aus und hält Transkripte nur kurz. Das Programm loggt keine Gesprächstexte, solange der Schalter „Transkripte“ aus ist.
- Gustav fragt nicht nach Nachnamen, Alter, Schule oder Adresse.
- Es gibt keine Kamera, und das ist Absicht.
- Ob und wie du das Gerät betreiben darfst, musst du selbst prüfen. Dieses Projekt ist keine Rechtsberatung.

**Kosten:** Gespräche verbrauchen ElevenLabs-Guthaben. Das Abendbudget (Standard 20 Minuten) begrenzt das.

## Aufbau des Repos

```
gehaeuse/   CAD (Python/manifold3d), STL, Snapmaker-Orca-Projekte, Vorschaubilder
software/   Programm, Web-Oberfläche, Installer, Clip-Texte, Agent-Prompt, Verkabelungsplan
docs/       Fotos vom Aufbau
```

## Lizenz

- **Software** (`software/`): [MIT](LICENSE)
- **Gehäuse, Druckdaten, Bilder, Texte** (`gehaeuse/`, `docs/`, Clip-Texte und Prompt): [CC BY-NC-SA 4.0](LICENSE-Druckdaten.md). Nachbauen und Verändern ist ausdrücklich erwünscht, aber nicht zum Verkauf, mit Namensnennung und unter derselben Lizenz.
- Schrift UnifrakturMaguntia: SIL Open Font License 1.1

Viel Spaß beim Nachbauen und ein schauriges Halloween! 🎃
