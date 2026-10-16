#!/usr/bin/env python3
"""Vorschaubilder mit kleinem Z-Buffer-Renderer: Frontansicht, zusammengebaut, Explosionsansicht."""
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import to_rgb
from matplotlib.path import Path
from matplotlib.patches import PathPatch
from shapely.geometry import Point
import grabstein as g

BG = np.array(to_rgb("#0d0f14"))


def draw2d(ax, geom, color, z=1):
    polys = geom.geoms if hasattr(geom, "geoms") else [geom]
    for p in polys:
        if p.is_empty or p.geom_type != "Polygon":
            continue
        verts, codes = [], []
        for ring in [p.exterior] + list(p.interiors):
            c = np.array(ring.coords)
            verts += c.tolist()
            codes += [Path.MOVETO] + [Path.LINETO] * (len(c) - 2) + [Path.CLOSEPOLY]
        ax.add_patch(PathPatch(Path(verts, codes), facecolor=color, edgecolor="none", zorder=z))


def render(parts, fname, azim=-35, elev=18, size=1100, glow_bg=True):
    a, e = np.radians(azim), np.radians(elev)
    # Kamera schaut aus Richtung 'eye' auf den Ursprung (orthografisch)
    fwd = -np.array([np.sin(a) * np.cos(e), -np.cos(a) * np.cos(e), np.sin(e)])
    right = np.cross(fwd, [0, 0, 1]); right /= np.linalg.norm(right)
    up = np.cross(right, fwd)
    light = -fwd * 0.6 + up * 0.5 + right * -0.4; light /= np.linalg.norm(light)
    allv = []
    data = []
    for m, col in parts:
        tm = g.to_trimesh(m)
        v = tm.vertices - np.array([0, 0, 140])
        P = np.stack([v @ right, v @ up, v @ fwd], 1)
        allv.append(P)
        data.append((P, tm.faces, tm.face_normals, np.array(to_rgb(col))))
    allP = np.vstack(allv)
    lo, hi = allP[:, :2].min(0), allP[:, :2].max(0)
    span = (hi - lo).max() * 1.08
    ctr = (hi + lo) / 2
    sc = size / span
    img = np.ones((size, size, 3)) * BG
    zb = np.full((size, size), np.inf)
    for P, F, N, col in data:
        S = np.empty_like(P)
        S[:, 0] = (P[:, 0] - ctr[0]) * sc + size / 2
        S[:, 1] = size / 2 - (P[:, 1] - ctr[1]) * sc
        S[:, 2] = P[:, 2]
        vis = (N @ fwd) < 0
        shade = 0.30 + 0.75 * np.clip(N @ light, 0, 1)
        for fi in np.nonzero(vis)[0]:
            t = S[F[fi]]
            x0, x1 = int(max(np.floor(t[:, 0].min()), 0)), int(min(np.ceil(t[:, 0].max()), size - 1))
            y0, y1 = int(max(np.floor(t[:, 1].min()), 0)), int(min(np.ceil(t[:, 1].max()), size - 1))
            if x1 < x0 or y1 < y0:
                continue
            xs, ys = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
            (ax_, ay), (bx, by), (cx, cy) = t[0, :2], t[1, :2], t[2, :2]
            den = (by - cy) * (ax_ - cx) + (cx - bx) * (ay - cy)
            if abs(den) < 1e-9:
                continue
            w0 = ((by - cy) * (xs - cx) + (cx - bx) * (ys - cy)) / den
            w1 = ((cy - ay) * (xs - cx) + (ax_ - cx) * (ys - cy)) / den
            w2 = 1 - w0 - w1
            inside = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6)
            if not inside.any():
                continue
            z = w0 * t[0, 2] + w1 * t[1, 2] + w2 * t[2, 2]
            sub = zb[y0:y1 + 1, x0:x1 + 1]
            m = inside & (z < sub)
            sub[m] = z[m]
            img[y0:y1 + 1, x0:x1 + 1][m] = np.clip(col * shade[fi] + 0.02, 0, 1)
    plt.imsave(fname, img)


if __name__ == "__main__":
    # Frontansicht 2D
    fig, ax = plt.subplots(figsize=(6, 8), dpi=150)
    fig.patch.set_facecolor(BG); ax.set_facecolor(BG)
    draw2d(ax, g.outline.difference(Point(*g.C).buffer(g.WEB_HOLE)), "#1e1e1e", 1)
    draw2d(ax, g.web.difference(g.spider).difference(g.eyes), "#ededed", 2)
    draw2d(ax, g.blood, "#c0141c", 3)
    draw2d(ax, g.spider, "#c0141c", 3)
    draw2d(ax, g.text, "#ffb21a", 4)
    draw2d(ax, g.eyes, "#ffb21a", 4)
    for bx, by in g.BOSSES:
        ax.add_patch(plt.Circle((bx, by), 3.4, color="#555", zorder=5))
    ax.set_xlim(-100, 100); ax.set_ylim(-10, 240); ax.set_aspect("equal"); ax.axis("off")
    plt.savefig("vorschau_front.png", bbox_inches="tight", facecolor=BG)
    plt.close()

    parts = g.assembled()
    render(parts, "vorschau_3d.png", azim=-32, elev=16)

    # Explosionsansicht: Frontplatte + Deckel nach vorne, Bodenplatte nach unten
    ex = []
    for i, (m, col) in enumerate(parts):
        if i in (0, 1, 2, 3):
            m = m.translate([0, -110, 0])
        elif i == 5:
            m = m.translate([0, -60, 0])
        elif i == 7:
            m = m.translate([0, 0, -45])
        ex.append((m, col))
    render(ex, "vorschau_explosion.png", azim=-48, elev=20)
