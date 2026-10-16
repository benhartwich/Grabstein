#!/usr/bin/env python3
"""
Flüsternder Grabstein – Halloween-Gehäuse für Snapmaker U1
Raspberry Pi 4 (im Sockel) + ReSpeaker XVF3800 (hinter dem Spinnennetz)
+ HC-SR501 Bewegungsmelder (Sockel vorne) + USB-Lautsprecher (im Grabstein).

Alle Maße in mm. Parameter oben anpassen, dann:  python3 grabstein.py
Benötigt: pip install manifold3d trimesh shapely matplotlib numpy
"""
import os
import numpy as np
import trimesh
from manifold3d import Manifold, CrossSection, FillRule, set_circular_segments
from shapely.geometry import Polygon, Point, box, LineString, MultiPolygon
from shapely.ops import unary_union
from shapely import affinity
from matplotlib.textpath import TextPath
from matplotlib.font_manager import FontProperties

set_circular_segments(64)
HERE = os.path.dirname(os.path.abspath(__file__))
FONT = os.path.join(HERE, "UnifrakturMaguntia.ttf")

# ---------------------------------------------------------------- Parameter
W = 170.0            # Breite Grabstein
H = 230.0            # Höhe Grabstein
HS = H - W / 2       # Höhe des geraden Teils (darüber Halbkreis-Bogen)
T = 4.0              # Stärke Frontplatte
INLAY = 1.0          # Tiefe der farbigen Einlagen (Schrift, Blut, Spinne)
D = 56.0             # Tiefe Korpus (Rückwand bis Frontplatte)
WALL = 3.0           # Wandstärke
TEXT_TOP = "R.I.P."
TEXT_BOTTOM = "Komm näher …"

# ReSpeaker XVF3800 (laut Händler max. 108 x 108 mm – bitte nachmessen!)
C = (0.0, 95.0)      # Mittelpunkt Netz / ReSpeaker auf der Frontplatte
POCKET = 111.0       # Innenmaß der Halterung
POCKET_H = 14.0      # Höhe der Halterungswände
WEB_R = 48.2         # offener Netzradius (Mikrofone sitzen bei ca. r = 43–47)
PCB_R = 50.0         # ReSpeaker XVF3800: Ø 100 mm (gemessen)
PCB_T = 1.6         # Platinenstärke
LEDGE_H = 2.0        # Abstand Platine – Netz
WEB_HOLE = 54.0      # Radius des weißen Netz-Einsatzes
HUB_R = 20.0         # geschlossene Netzmitte (Spinne)

# Sockel
PW, PD, PH = 200.0, 100.0, 50.0
YF = -22.0           # Welt-y der Grabstein-Vorderseite auf dem Sockel

SCREW_HOLE = 1.3     # Kernloch M3 (selbstschneidend)
SCREW_CLEAR = 1.7    # Durchgang M3

# ---------------------------------------------------------------- Helfer
def cs(geom):
    """shapely (Multi)Polygon -> CrossSection"""
    if geom.is_empty:
        return CrossSection()
    polys = geom.geoms if hasattr(geom, "geoms") else [geom]
    contours = []
    for p in polys:
        if p.is_empty or p.geom_type != "Polygon":
            continue
        contours.append(np.array(p.exterior.coords)[:-1])
        for r in p.interiors:
            contours.append(np.array(r.coords)[:-1])
    return CrossSection(contours, FillRule.EvenOdd)


def ext(geom, h, z0=0.0):
    return cs(geom).extrude(h).translate([0, 0, z0])


def remap(m, rows):
    """beliebige affine Abbildung (3x4)"""
    return m.transform(np.array(rows, dtype=float))


def text_poly(txt, height, center):
    tp = TextPath((0, 0), txt, size=100, prop=FontProperties(fname=FONT))
    outs, holes = [], []
    for v in tp.to_polygons():
        if len(v) < 3:
            continue
        p = Polygon(v).buffer(0)
        if p.is_empty:
            continue
        a = 0.5 * np.sum(v[:-1, 0] * v[1:, 1] - v[1:, 0] * v[:-1, 1])
        (outs if a < 0 else holes).append(p)
    g = unary_union(outs).difference(unary_union(holes)) if holes else unary_union(outs)
    if g.area < 1e-6:  # Konturrichtung andersherum
        g = unary_union(holes).difference(unary_union(outs))
    minx, miny, maxx, maxy = g.bounds
    s = height / (maxy - miny)
    g = affinity.scale(g, s, s, origin=(0, 0))
    minx, miny, maxx, maxy = g.bounds
    return affinity.translate(g, center[0] - (minx + maxx) / 2, center[1] - (miny + maxy) / 2)


def fit_width(g, maxw):
    minx, miny, maxx, maxy = g.bounds
    if maxx - minx <= maxw:
        return g
    s = maxw / (maxx - minx)
    return affinity.scale(g, s, s, origin="center")


def teardrop(r, cx, cy, up=1):
    """Loch mit Spitze in Druckrichtung nach oben (stützfrei)"""
    c = Point(cx, cy).buffer(r)
    tri = Polygon([(cx - r * 0.707, cy + up * r * 0.707), (cx + r * 0.707, cy + up * r * 0.707), (cx, cy + up * r * 1.414)])
    return c.union(tri)


# ---------------------------------------------------------------- 2D-Formen
outline = box(-W / 2, 0, W / 2, HS).union(Point(0, HS).buffer(W / 2, resolution=64))
outline = outline.intersection(box(-W, 0, W, H + 1))
inner = outline.buffer(-WALL, join_style=2)

RB = W / 2 - WALL - 4.0
BOSSES = [(-RB, WALL + 4.0), (RB, WALL + 4.0),
          (-RB * np.cos(np.radians(30)), HS + RB * np.sin(np.radians(30))),
          (RB * np.cos(np.radians(30)), HS + RB * np.sin(np.radians(30)))]

# Schrift
t_top = fit_width(text_poly(TEXT_TOP, 30, (0, 180)), 112)
t_bot = fit_width(text_poly(TEXT_BOTTOM, 15, (0, 22)), 128)
text = unary_union([t_top, t_bot]).buffer(0.25).intersection(outline.buffer(-4))  # Haarlinien druckbar machen
# Die Sichtseite liegt beim Druck unten -> Schrift spiegeln, damit sie von vorne lesbar ist
text = affinity.scale(text, -1, 1, origin=(0, 0))

# Blut am Bogen
band = outline.difference(outline.buffer(-3.2)).intersection(box(-W, HS - 2, W, H + 2))
drips = [band]
for x, L in [(-66, 9), (-52, 18), (-37, 11), (-22, 26), (-7, 15), (9, 31), (24, 13), (38, 21), (53, 10), (66, 15)]:
    ytop = HS + np.sqrt((W / 2) ** 2 - x ** 2) - 1.5
    w0 = 4.2
    body = Polygon([(x - w0 / 2, ytop), (x + w0 / 2, ytop), (x + 1.3, ytop - L), (x - 1.3, ytop - L)])
    drips += [body, Point(x, ytop - L).buffer(2.0)]
blood = unary_union(drips).intersection(outline)
blood = blood.difference(text.buffer(1.5))
for bx, by in BOSSES:
    blood = blood.difference(Point(bx, by).buffer(4.5))

# Spinnennetz (lokal um 0,0 – wird nach C verschoben)
def web_geometry():
    thr = 1.0  # halbe Fadenstärke -> 2 mm
    spokes = [LineString([(0, 0), (60 * np.cos(np.radians(a)), 60 * np.sin(np.radians(a)))]).buffer(thr)
              for a in range(0, 360, 30)]
    rings = []
    for r, sag in [(27, 1.2), (36, 1.5), (45, 1.8)]:
        pts = []
        for k in range(12):
            a0, a1 = np.radians(30 * k), np.radians(30 * (k + 1))
            am = (a0 + a1) / 2
            rm = r * np.cos(np.radians(15)) - sag
            pts += [(r * np.cos(a0), r * np.sin(a0)), (rm * np.cos(am), rm * np.sin(am))]
        pts.append(pts[0])
        rings.append(LineString(pts).buffer(thr))
    threads = unary_union(spokes + rings + [Point(0, 0).buffer(HUB_R)])
    openings = Point(0, 0).buffer(WEB_R).difference(threads)
    return Point(0, 0).buffer(WEB_HOLE).difference(openings)

web = affinity.translate(web_geometry(), *C)

# Spinne in der Netzmitte
def spider_geometry():
    parts = [affinity.scale(Point(0, -3.5).buffer(1), 6.2, 7.8, origin=(0, -3.5)),
             Point(0, 4.6).buffer(3.8)]
    legs = []
    for s in (1, -1):
        for (a, b, c) in [((3, 6), (9, 11), (13, 16)), ((4, 4.5), (11, 6), (16.5, 9)),
                          ((4, 3), (11, 0), (16.5, -3)), ((3.5, 1.5), (10, -7), (13, -14))]:
            legs.append(LineString([(s * a[0], a[1]), (s * b[0], b[1]), (s * c[0], c[1])]).buffer(0.85))
    body = unary_union(parts + legs).intersection(Point(0, 0).buffer(HUB_R - 1.2))
    eyes = unary_union([Point(-1.5, 5.6).buffer(1.0), Point(1.5, 5.6).buffer(1.0)])
    return body.difference(eyes), eyes

spider, eyes = spider_geometry()
spider = affinity.translate(spider, *C)
eyes = affinity.translate(eyes, *C)

# ---------------------------------------------------------------- Frontplatte (Druck: Sichtseite nach unten)
def screw_csk(x, y):
    hole = Manifold.cylinder(T + 2, SCREW_CLEAR).translate([x, y, -1])
    cone = Manifold.cylinder(1.8, 3.4, SCREW_CLEAR).translate([x, y, -0.2])
    return hole + cone

plate = ext(outline, T)
plate -= ext(Point(*C).buffer(WEB_HOLE), T + 2, -1)
plate -= ext(text, INLAY + 0.01, -0.01) + ext(blood, INLAY + 0.01, -0.01)
for b in BOSSES:
    plate -= screw_csk(*b)

# Halterung ReSpeaker auf der Rückseite
pk_out = box(C[0] - POCKET / 2 - 3, C[1] - POCKET / 2 - 3, C[0] + POCKET / 2 + 3, C[1] + POCKET / 2 + 3)
pk_in = box(C[0] - POCKET / 2, C[1] - POCKET / 2, C[0] + POCKET / 2, C[1] + POCKET / 2)
pocket = ext(pk_out.difference(pk_in), POCKET_H, T)
notch = unary_union([box(C[0] - 23, C[1] - 70, C[0] + 23, C[1] + 70), box(C[0] - 70, C[1] - 23, C[0] + 70, C[1] + 23)])
pocket -= ext(notch, POCKET_H, T + 2.0)
CB = POCKET / 2 + 6.5
for sx in (-1, 1):
    for sy in (-1, 1):
        bx, by = C[0] + sx * CB, C[1] + sy * CB
        pocket += Manifold.cylinder(POCKET_H, 4.5).translate([bx, by, T])
        pocket -= Manifold.cylinder(POCKET_H, SCREW_HOLE).translate([bx, by, T + 4])
plate_black = plate + pocket

web_white = ext(web, T)
web_white -= ext(spider.union(eyes), INLAY + 0.01, -0.01)
def sector(r0, r1, a0, a1, n=48):
    """Ringsektor im lokalen Koordinatensystem (Winkel in Grad)"""
    a = np.radians(np.linspace(a0, a1, n))
    pts = [(r1 * np.cos(t), r1 * np.sin(t)) for t in a] + [(r0 * np.cos(t), r0 * np.sin(t)) for t in a[::-1]]
    return Polygon(pts)

# Auflagering unter dem Platinenrand (Lücke unten für USB-C und Klinke)
ledge = sector(PCB_R - 1.4, PCB_R + 0.2, -55, 235, 160)
# Zentrierstege außen am Platinenrand – halten die Platine mittig, reichen nur bis unter die Bauteile
posts = unary_union([sector(PCB_R + 0.4, PCB_R + 2.0, a - 5, a + 5, 12) for a in (90, 215, 310)])
web_white += ext(affinity.translate(ledge, *C), LEDGE_H, T)
web_white += ext(affinity.translate(posts, *C), LEDGE_H + 0.8, T)
# Pfeil auf dem Ring: hier zeigt der USB-C-Anschluss hin
arrow = Polygon([(-2.5, PCB_R + 1.5), (2.5, PCB_R + 1.5), (0, PCB_R + 3.8)])
arrow = affinity.rotate(arrow, 180, origin=(0, 0))
web_white += ext(affinity.translate(arrow, *C), 0.4, T)

blood_red = ext(blood, INLAY) + ext(spider, INLAY)
glow = ext(text, INLAY) + ext(eyes, INLAY)

# Klemmdeckel für den ReSpeaker
cap_g = box(-CB - 6, -CB - 6, CB + 6, CB + 6).difference(Point(0, 0).buffer(PCB_R - 4.5))
cap = ext(cap_g, 3.0)
# Andruckstifte: drücken auf den bauteilfreien Platinenrand (0,3 mm Vorspannung)
CLAMP_R = PCB_R - 1.0
CLAMP_L = POCKET_H - LEDGE_H - PCB_T + 0.3
for a in (45, 135, 225, 315):
    cap += Manifold.cylinder(CLAMP_L + 0.01, 1.6).translate([CLAMP_R * np.cos(np.radians(a)), CLAMP_R * np.sin(np.radians(a)), 2.99])
for sx in (-1, 1):
    for sy in (-1, 1):
        cap -= Manifold.cylinder(5, SCREW_CLEAR).translate([sx * CB, sy * CB, -1])

# Regendach über dem Netz (separat gedruckt, von vorne aufkleben)
hood = ext(sector(WEB_HOLE + 1.5, WEB_HOLE + 7.0, 12, 168, 120), 14.0)
hood += ext(sector(WEB_HOLE + 5.5, WEB_HOLE + 7.0, 12, 168, 120), 16.0)   # Tropfkante

# ---------------------------------------------------------------- Korpus (Druck: Rückwand unten)
shell = ext(outline, D) - ext(inner, D + 1, WALL)
for bx, by in BOSSES:
    shell += Manifold.cylinder(D, 4.5).translate([bx, by, 0])
    shell -= Manifold.cylinder(15, SCREW_HOLE).translate([bx, by, D - 14])
# Kabeldurchführung + Schrauben im Boden (Wand bei y = 0..3)
shell -= Manifold.cube([40, 6, 30]).translate([10, -1.5, 13])
for sx in (-60, 60):
    shell -= remap(Manifold.cylinder(6, SCREW_CLEAR), [[1, 0, 0, sx], [0, 0, 1, -1.5], [0, 1, 0, 28]])
# Risse in den Seitenwänden (Licht vom LED-Ring + Schall)
cracks2d = unary_union([
    LineString([(25, 8), (36, 20), (31, 29), (45, 41), (42, 48)]).buffer(1.1),
    LineString([(70, 10), (80, 21), (76, 30), (92, 46)]).buffer(1.1),
    LineString([(108, 9), (117, 18), (113, 27), (126, 38), (124, 47)]).buffer(1.1),
])
crack3d = cs(cracks2d).extrude(12)
for sx in (-1, 1):
    # (u,v,w) -> (x = w + offs, y = u, z = v)
    shell -= remap(crack3d, [[0, 0, 1, sx * (W / 2) - 6], [1, 0, 0, 0], [0, 1, 0, 0]])

# ---------------------------------------------------------------- Sockel (Weltkoordinaten)
P_IN_X, P_IN_Y = PW / 2 - WALL, PD / 2 - WALL
plinth = Manifold.cube([PW, PD, PH]).translate([-PW / 2, -PD / 2, 0])
plinth -= Manifold.cube([PW - 2 * WALL, PD - 2 * WALL, PH - WALL + 1]).translate([-P_IN_X, -P_IN_Y, -1])
PB = [(sx * (P_IN_X - 4), sy * (P_IN_Y - 4)) for sx in (-1, 1) for sy in (-1, 1)]
for bx, by in PB:
    plinth += Manifold.cylinder(PH - WALL - 3, 5).translate([bx, by, 3])
    plinth -= Manifold.cylinder(14, SCREW_HOLE).translate([bx, by, 2])
# Verbindung zum Grabstein
y_hole0 = YF + T + D - 43
plinth -= Manifold.cube([40, 30, 10]).translate([10, y_hole0, PH - 6])
y_scr = YF + T + D - 28
for sx in (-60, 60):
    plinth += Manifold.cylinder(PH - WALL - 40, 6).translate([sx, y_scr, 40])
    plinth -= Manifold.cylinder(PH, SCREW_CLEAR).translate([sx, y_scr, 0])
    plinth -= Manifold.cylinder(3.0, 3.35, circular_segments=6).translate([sx, y_scr, 39.9])
# PIR-Sensor HC-SR501 vorne (Linse Ø23, Platine 32,3 x 24,3)
pir_hole = teardrop(11.9, 0, 25, up=-1)            # Spitze zeigt im Druck (kopfüber) nach oben
plinth -= remap(cs(pir_hole).extrude(10), [[1, 0, 0, 0], [0, 0, 1, -PD / 2 - 2], [0, 1, 0, 0]])
for sx in (-1, 1):
    rail = Manifold.cube([6, 7, PH - WALL - 10]).translate([sx * 17 - 3, -P_IN_Y, 10])
    groove = Manifold.cube([3.1, 1.9, 38.4]).translate([sx * 15.05 - 1.55, -P_IN_Y + 2.0, -1])
    plinth += rail
    plinth -= groove
# Rückseite: Strom-Pi, Strom-Lautsprecher, Kippschalter Kinder-Modus
plinth -= Manifold.cube([26, 10, 16]).translate([-86, PD / 2 - 5, 6])
plinth -= Manifold.cube([22, 10, 12]).translate([20, PD / 2 - 5, 12])
plinth -= remap(cs(teardrop(3.1, 70, 30, up=-1)).extrude(10), [[1, 0, 0, 0], [0, 0, 1, PD / 2 - 5], [0, 1, 0, 0]])
# Lüftungsschlitze seitlich
for sx in (-1, 1):
    for yy in (-24, -12, 0, 12, 24):
        plinth -= Manifold.cube([10, 3, 22]).translate([sx * PW / 2 - 5, yy - 1.5, 12])

# Bodenplatte mit Pi-4-Abstandshaltern
bottom = ext(box(-P_IN_X + 0.3, -P_IN_Y + 0.3, P_IN_X - 0.3, P_IN_Y - 0.3), 3)
for bx, by in PB:
    bottom -= Manifold.cylinder(5, SCREW_CLEAR).translate([bx, by, -1])
    bottom -= Manifold.cylinder(1.8, 3.4, SCREW_CLEAR).translate([bx, by, -0.2])
PI_X0, PI_YEDGE = -85.0, 40.0           # Pi-Platine: x -85..0, Port-Kante (USB-C) bei y = 40
PI_HOLES = [(PI_X0 + hx, PI_YEDGE - hy) for hx in (3.5, 61.5) for hy in (3.5, 52.5)]
for hx, hy in PI_HOLES:
    bottom += Manifold.cylinder(6, 3.0).translate([hx, hy, 3])
    bottom -= Manifold.cylinder(6, 1.05).translate([hx, hy, 4])   # Kernloch M2,5
for i in range(6):
    bottom -= Manifold.cube([3, 30, 5]).translate([-72 + i * 9, -2, -1])

# ---------------------------------------------------------------- Export
PARTS_PRINT = {
    "1_Frontplatte_A_schwarz": plate_black,
    "1_Frontplatte_B_weiss_Netz": web_white,
    "1_Frontplatte_C_rot_Blut_Spinne": blood_red,
    "1_Frontplatte_D_leucht_Schrift_Augen": glow,
    "2_Korpus_schwarz": shell,
    "3_Sockel_schwarz": remap(plinth, [[1, 0, 0, 0], [0, -1, 0, 0], [0, 0, -1, PH]]),
    "4_Bodenplatte_schwarz": bottom,
    "5_Klemmdeckel_schwarz": cap,
    "6_Regendach_schwarz": hood,
}

def to_trimesh(m):
    mesh = m.to_mesh()
    return trimesh.Trimesh(vertices=np.array(mesh.vert_properties)[:, :3], faces=np.array(mesh.tri_verts), process=False)

def assembled():
    """Teile in Weltkoordinaten für die Vorschau"""
    to_world_plate = [[-1, 0, 0, 0], [0, 0, 1, YF], [0, 1, 0, PH]]         # echte Drehung (keine Spiegelung): (x,y_p,z_p) -> (-x, YF+z_p, PH+y_p)
    to_world_shell = [[1, 0, 0, 0], [0, 0, -1, YF + T + D], [0, 1, 0, PH]]
    cap_plate = remap(cap, [[1, 0, 0, C[0]], [0, 1, 0, C[1]], [0, 0, -1, T + POCKET_H + 3.0]])  # Stifte zeigen nach vorne
    return [
        (remap(plate_black, to_world_plate), "#202020"),
        (remap(web_white, to_world_plate), "#f2f2f2"),
        (remap(blood_red, to_world_plate), "#c0141c"),
        (remap(glow, to_world_plate), "#ffb21a"),
        (remap(shell, to_world_shell), "#2b2b2b"),
        (remap(cap_plate, to_world_plate), "#303030"),
        (plinth, "#262626"),
        (bottom, "#333333"),
    ]

if __name__ == "__main__":
    out = os.path.join(HERE, "stl")
    os.makedirs(out, exist_ok=True)
    for name, m in PARTS_PRINT.items():
        tm = to_trimesh(m)
        b = tm.bounds
        print(f"{name:40s} ok={tm.is_watertight!s:5s} vol={tm.volume/1000:7.1f} cm3  "
              f"size={b[1][0]-b[0][0]:.0f}x{b[1][1]-b[0][1]:.0f}x{b[1][2]-b[0][2]:.0f} z0={b[0][2]:.2f}")
        tm.export(os.path.join(out, name + ".stl"))
