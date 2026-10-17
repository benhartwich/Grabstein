#!/usr/bin/env python3
"""Erzeugt den Abschnitt „So steckt es bei dir“ in verkabelung.html neu.

Die Ansichten zeigen den tatsächlichen Aufbau mit den echten Kabelfarben, jeweils aus derselben
Richtung wie die Fotos in docs/. Nach Änderungen an der Verkabelung hier anpassen und starten:
    python3 software/verkabelung_ansichten.py
"""
from pathlib import Path

PAGE = Path(__file__).with_name("verkabelung.html")

# ---------------------------------------------------------------- Ansicht 1: Stiftleiste (USB unten, Leiste rechts)
P = 26              # Pin-Abstand im Bild
TOP = 70            # y von Reihe 1
XI, XO = 286, 316   # innere (ungerade) und äußere (gerade) Reihe
# Pin, Reihe, außen?, Farbe, Signal, Ziel, Farbname
WIRES = [
    (2, 1, True, "orange", "5 V", "Bewegungsmelder VCC", "orange"),
    (4, 2, True, "grey", "5 V", "Radar VCC", "grau"),
    (6, 3, True, "brown", "GND", "Bewegungsmelder GND", "braun"),
    (8, 4, True, "a", "TXD", "Radar RX (neu)", "Kabel A"),
    (10, 5, True, "b", "RXD", "Radar TX (neu)", "Kabel B"),
    (9, 5, False, "white", "GND", "Radar GND", "weiß"),
    (11, 6, False, "red", "GPIO17", "Bewegungsmelder OUT", "rot"),
    (15, 8, False, "black", "GPIO22", "Radar OUT", "schwarz"),
]


def row_y(r: int) -> int:
    return TOP + (r - 1) * P


def wire(x: float, y1: float, y2: float, col: str) -> str:
    d = f"M{x} {y1} V{y2}"
    return f'<path d="{d}" class="wire-o"/><path d="{d}" class="wire" style="stroke: var(--k-{col})"/>'


def pi_view() -> str:
    a = ['<svg viewBox="150 20 530 610" role="img" aria-label="GPIO-Leiste von oben, USB-Buchsen unten, mit den gesteckten Kabeln">',
         '<rect x="0" y="24" width="356" height="600" rx="16" style="fill: var(--board)"/>',
         '<rect x="160" y="34" width="80" height="14" rx="3" style="fill: var(--board-ink); fill-opacity:.55"/>',
         '<text x="200" y="66" text-anchor="middle" class="t-board" style="font-size:11px">Display</text>']
    for x, w, lbl in ((160, 70, "USB"), (240, 106, "LAN")):
        a.append(f'<rect x="{x}" y="584" width="{w}" height="40" rx="4" style="fill: var(--board-ink); fill-opacity:.85"/>')
        a.append(f'<text x="{x + w / 2}" y="609" text-anchor="middle" style="fill: var(--board); font-size: 12px; font-weight: 600">{lbl}</text>')
    a.append('<text x="205" y="330" text-anchor="middle" class="t-mod-title" style="fill: var(--board-ink); font-size: 15px" '
             'transform="rotate(-90 205 330)">Raspberry Pi 4 · von oben</text>')
    a.append(f'<rect x="{XI - 16}" y="{TOP - 15}" width="{XO - XI + 32}" height="{19 * P + 30}" rx="4" style="fill: #151515"/>')
    a.append(f'<text x="{XI}" y="{TOP - 22}" text-anchor="middle" class="t-board" style="font-size:10.5px">innen</text>')
    a.append(f'<text x="{XO}" y="{TOP - 22}" text-anchor="middle" class="t-board" style="font-size:10.5px">außen</text>')
    for r in range(1, 21):
        for x in (XI, XO):
            a.append(f'<circle cx="{x}" cy="{row_y(r)}" r="4.5" style="fill: var(--pin)"/>')
    a.append(f'<text x="{XI - 24}" y="{row_y(1) + 4}" text-anchor="end" class="t-board" style="font-size:12px; font-weight:600">Pin 1</text>')
    a.append(f'<text x="{XI - 24}" y="{row_y(20) + 4}" text-anchor="end" class="t-board" style="font-size:11px">Pin 39</text>')
    # Labels rechts untereinander, auch wenn zwei Kabel in derselben Reihe stecken
    labels = sorted(WIRES, key=lambda w: (w[1], not w[2]))
    for i, (pin, r, outer, col, _sig, dest, name) in enumerate(labels):
        x, y = (XO if outer else XI), row_y(r)
        ly = TOP + i * P
        d = f"M{x} {y} H352 L366 {ly} H372"
        a.append(f'<path d="{d}" class="wire-o"/><path d="{d}" class="wire" style="stroke: var(--k-{col})"/>')
        a.append(f'<circle cx="{x}" cy="{y}" r="7" style="fill: var(--k-{col}); stroke: var(--ink); stroke-width: 1.5"/>')
        a.append(f'<text x="380" y="{ly + 4.5}" class="t-wire"><tspan font-weight="600">Pin {pin}</tspan> · {name} → {dest}</text>')
    a.append(f'<text x="380" y="{row_y(11)}" class="t-muted">Rest der Leiste bleibt frei.</text>')
    a.append(f'<text x="380" y="{row_y(13)}" class="t-muted">Innen: ungerade Pins (1, 3, 5 …)</text>')
    a.append(f'<text x="380" y="{row_y(14)}" class="t-muted">Außen am Rand: gerade (2, 4, 6 …)</text>')
    a.append('</svg>')
    return "\n".join(a)


# ---------------------------------------------------------------- Ansicht 2: Radar, Bauteilseite, Stifte oben
def radar_view() -> str:
    b = ['<svg viewBox="0 0 520 330" role="img" aria-label="Radar LD2410C von der Bauteilseite mit fünf gesteckten Kabeln">',
         '<rect x="70" y="150" width="320" height="160" rx="14" style="fill: #1f6fb5"/>',
         '<rect x="88" y="182" width="44" height="26" rx="3" style="fill: #1b1b1b"/>',
         '<text x="110" y="226" text-anchor="middle" style="fill:#d9e8f7; font-size:10px">Regler</text>',
         '<rect x="200" y="220" width="44" height="44" rx="4" style="fill: #1b1b1b"/>',
         '<text x="300" y="250" style="fill:#d9e8f7; font-size:13px; font-weight:600">HLK-LD2410C</text>']
    pins = [("VCC", "grey", "grau", "Pin 4"), ("GND", "white", "weiß", "Pin 9"), ("OUT", "black", "schwarz", "Pin 15"),
            ("RX", "a", "Kabel A", "Pin 8 (neu)"), ("TX", "b", "Kabel B", "Pin 10 (neu)")]
    for i, (lbl, col, name, dest) in enumerate(pins):
        x = 130 + i * 40
        b.append(f'<rect x="{x - 7}" y="146" width="14" height="14" style="fill: #111"/>')
        b.append(f'<text x="{x}" y="180" text-anchor="middle" style="fill:#ffffff; font-family: var(--font-mono); font-size:11px; font-weight:600">{lbl}</text>')
        ly = 22 + i * 24
        b.append(wire(x, 146, ly, col))
        b.append(f'<text x="{x + 12}" y="{ly + 4}" class="t-wire"><tspan font-weight="600">{name}</tspan> → {dest}</text>')
    b.append('</svg>')
    return "\n".join(b)


# ---------------------------------------------------------------- Ansicht 3: Bewegungsmelder, Bauteilseite, Stifte oben
def pir_view() -> str:
    c = ['<svg viewBox="0 0 460 400" role="img" aria-label="Bewegungsmelder HC-SR501 von der Bauteilseite mit drei gesteckten Kabeln">',
         '<rect x="70" y="150" width="320" height="200" rx="10" style="fill: #1d5f6b"/>',
         '<rect x="92" y="190" width="40" height="90" rx="4" style="fill: #1b1b1b"/>',
         '<text x="112" y="300" text-anchor="middle" style="fill:#d7eef1; font-size:10px">Chip</text>',
         '<rect x="306" y="168" width="22" height="9" rx="4" style="fill: #d8322f"/>',
         '<text x="317" y="194" text-anchor="middle" style="fill:#d7eef1; font-size:10px">Diode</text>',
         '<rect x="300" y="206" width="40" height="24" rx="3" style="fill: #1b1b1b"/>',
         '<text x="320" y="246" text-anchor="middle" style="fill:#d7eef1; font-size:10px">Regler</text>']
    for x, lbl in ((180, "Reichweite"), (270, "Haltezeit")):
        c.append(f'<rect x="{x - 24}" y="290" width="48" height="48" rx="4" style="fill: #111"/><circle cx="{x}" cy="314" r="11" style="fill: #e3a72f"/>')
        c.append(f'<text x="{x}" y="372" text-anchor="middle" class="t-muted">{lbl}</text>')
    c.append('<rect x="86" y="300" width="20" height="34" rx="3" style="fill: #f2c318"/>')
    c.append('<text x="96" y="372" text-anchor="middle" class="t-muted">Jumper</text>')
    for i, (lbl, col, name, dest) in enumerate([("GND", "brown", "braun", "Pin 6"), ("OUT", "red", "rot", "Pin 11"), ("VCC", "orange", "orange", "Pin 2")]):
        x = 190 + i * 40
        c.append(f'<rect x="{x - 7}" y="146" width="14" height="14" style="fill: #111"/>')
        c.append(f'<text x="{x}" y="178" text-anchor="middle" style="fill:#ffffff; font-family: var(--font-mono); font-size:11px; font-weight:600">{lbl}</text>')
        ly = 30 + i * 30
        c.append(wire(x, 146, ly, col))
        c.append(f'<text x="{x + 12}" y="{ly + 4}" class="t-wire"><tspan font-weight="600">{name}</tspan> {lbl} → {dest}</text>')
    c.append('</svg>')
    return "\n".join(c)


def section() -> str:
    return f'''
  <section class="panel" id="bei-dir">
    <h2>So steckt es bei dir</h2>
    <p class="lead" style="margin-bottom: 12px">Geprüft am 17.10.: alles richtig gesteckt. Die Bilder zeigen jeweils <b>dieselbe Ansicht wie deine Fotos</b> und deine echten Kabelfarben.</p>
    <ul class="status" style="margin-bottom: 12px">
      <li>Pi-Seite gemessen: nur GPIO17 und GPIO22 werden von außen auf „high“ gezogen, die Nachbar-Pins bleiben „low“.</li>
      <li>Radar laut Hersteller-Datenblatt: grau VCC, weiß GND, schwarz OUT – keine Datenflanken auf GPIO22, also wirklich OUT.</li>
      <li>Bewegungsmelder schaltet (gemessen mehrfach zwischen 08:39 und 08:48).</li>
      <li>Radar meldet dauerhaft „jemand da“, solange jemand im Raum ist – es erkennt auch Atmen bis ca. 5 m.</li>
    </ul>
    <p class="lead" style="margin: 0 0 18px"><b>Neu: zwei Kabel für den Radar-Abstand.</b> Kabel A (lila gezeichnet) vom Radar-RX an Pin 8, Kabel B (gelb gezeichnet) vom Radar-TX an Pin 10 – beide auf der Außenreihe direkt unter dem braunen Kabel. Deine echten Farben dürfen anders sein. Merkhilfe: Die Kabel kreuzen sich – der äußerste Radar-Stift (TX) kommt an den tieferen Pin (10).</p>
    <div class="views">
      <div class="view">
        <h3>1 · Stiftleiste am Pi</h3>
        <p class="from">Blick <b>von oben</b>, <b>USB-Buchsen unten</b>, Stiftleiste am <b>rechten Rand</b> (wie dein erstes Foto). Pin 1 ist oben in der inneren Reihe.</p>
        {pi_view()}
      </div>
      <div class="stack" style="display:grid; gap:18px">
        <div class="view">
          <h3>2 · Radar LD2410C</h3>
          <p class="from">Blick auf die <b>Bauteilseite</b> (Chip, Aufdruck „HLK-LD2410C“), <b>Stifte oben</b> (wie dein Foto). VCC liegt neben dem dreibeinigen Spannungsregler. Auf der Rückseite steht die Beschriftung gespiegelt: TX RX OUT GND VCC.</p>
          {radar_view()}
        </div>
        <div class="view">
          <h3>3 · Bewegungsmelder HC-SR501</h3>
          <p class="from">Blick auf die <b>Bauteilseite</b> (Kuppel zeigt von dir weg), <b>Stifte oben</b>, die zwei Drehregler unten. Von links: <b>braun GND – rot OUT – orange VCC</b>. VCC erkennst du an der kleinen roten Diode und dem dreibeinigen Regler rechts daneben. Von der Kuppelseite aus gesehen ist es gespiegelt.</p>
          <ul class="checks" style="font-size:.88rem">
            <li><strong>Haltezeit</strong> (rechter Regler) ganz nach links: ca. 3 s. Die Pausen macht das Programm selbst.</li>
            <li><strong>Reichweite</strong> (linker Regler) erstmal in der Mitte. Gelben Jumper so lassen.</li>
            <li>Bleibt er nach dem Drehen ewig an, sind die Regler bei deinem Exemplar vertauscht – dann den anderen nehmen.</li>
          </ul>
          {pir_view()}
        </div>
      </div>
    </div>
  </section>
'''


if __name__ == "__main__":
    html = PAGE.read_text()
    start = html.index('\n  <section class="panel" id="bei-dir">')
    end = html.index("</section>", start) + len("</section>\n")
    PAGE.write_text(html[:start] + section().rstrip("\n") + "\n" + html[end:])
    print(f"{PAGE.name}: Abschnitt „So steckt es bei dir“ neu erzeugt")
