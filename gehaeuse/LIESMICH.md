# Gehäuse – Flüsternder Grabstein (Snapmaker U1)

Grabstein 170 × 230 mm auf Sockel 200 × 100 × 50 mm, Gesamthöhe 280 mm. Parametrisch in Python (`grabstein.py`, manifold3d).

## Drucken

Fertige Druckdateien in `druck/` (Snapmaker Orca, alle Filament-Plätze auf PLA stellen):

| Datei | Inhalt | Farben |
|---|---|---|
| `Druck_1_Frontplatte_4Farben.3mf` | Frontplatte mit Netz, Schrift, Blut, Spinne, ReSpeaker-Halterung | 1 schwarz, 2 weiß, 3 gelb/leuchtend, 4 rot |
| `Druck_2_Korpus.3mf` | Grabstein-Korpus | schwarz |
| `Druck_3_Sockel.3mf` | Sockel (liegt kopfüber) | schwarz |
| `Druck_4_Bodenplatte_Klemmdeckel.3mf` | Bodenplatte (der Klemmdeckel darin ist veraltet) | schwarz |
| `Druck_5_Klemmdeckel_neu_Regendach.3mf` | Klemmdeckel mit Andruckstiften, Regendach | schwarz |

Die Frontplatte liegt mit der Sichtseite auf dem Bett; die Schrift ist im Modell gespiegelt und von vorne richtig lesbar. Keine Stützen nötig. 0,2–0,24 mm Schichthöhe, 3 Wände, 15 % Infill.

## Schrauben

- 4 × M3 × 16 Senkkopf – Frontplatte an Korpus
- 4 × M3 × 10 – Klemmdeckel auf ReSpeaker-Halterung
- 2 × M3 × 16 + 2 Muttern M3 – Korpus auf Sockel (Muttern in die Sechskant-Taschen im Sockel)
- 4 × M3 × 10 Senkkopf – Bodenplatte
- 4 × M2,5 × 6 – Raspberry Pi 4

## Montage

1. **Sockel:** Bewegungsmelder mit der Linse ins runde Loch vorne, Platine mit Heißkleber auf den Schienen fixieren. Radar innen an die Vorderwand kleben (Antennenseite nach außen). M3-Muttern in die Sechskant-Taschen.
2. **Korpus** auf den Sockel, offene Seite nach vorne; 2 × M3 × 16 von innen.
3. **Raspberry Pi** auf die Bodenplatte; USB-C zur großen Öffnung in der Rückwand. Verkabeln, Bodenplatte von unten verschrauben.
4. **Frontplatte:** optional imprägnierten Lautsprecherstoff (Ø 96 mm) innen aufs Netz kleben. ReSpeaker mit der Logo-Seite nach unten auf den Auflagering legen, USB-C zeigt zum Pfeil. Klemmdeckel mit den Stiften nach unten verschrauben.
5. **Regendach** von vorne über das Netz kleben.
6. Lautsprecher anschließen, Frontplatte auf den Korpus schrauben.

## Anpassen

Maße oben in `grabstein.py` ändern, dann `python3 grabstein.py && python3 vorschau.py && python3 make_3mf.py` (benötigt `pip install manifold3d trimesh shapely matplotlib numpy`).

Schrift: UnifrakturMaguntia (SIL Open Font License 1.1).
