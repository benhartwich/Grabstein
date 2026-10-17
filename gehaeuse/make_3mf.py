#!/usr/bin/env python3
"""Erzeugt ein fertiges Projekt (.3mf) für Snapmaker Orca / OrcaSlicer:
4 Druckplatten, Teile richtig ausgerichtet, Farben (Extruder 1-4) bereits zugewiesen.

Extruder/Werkzeugkopf (wie am U1 geladen): 1 = schwarz, 2 = weiß, 3 = gelb/leuchtend, 4 = rot
"""
import os
import uuid
import zipfile
import numpy as np
import grabstein as g

BED = 270.0
STRIDE = BED * 1.2            # Abstand der Platten im Orca-Projekt
COLS = 2

PLATES = [
    ("Frontplatte – 4 Farben", [
        ("Frontplatte", [("A_schwarz", "1_Frontplatte_A_schwarz", 1),
                         ("B_weiss_Netz", "1_Frontplatte_B_weiss_Netz", 2),
                         ("C_rot_Blut_Spinne", "1_Frontplatte_C_rot_Blut_Spinne", 4),
                         ("D_leucht_Schrift_Augen", "1_Frontplatte_D_leucht_Schrift_Augen", 3)], (0, 0)),
    ]),
    ("Grabstein-Korpus", [("Korpus", [("Korpus", "2_Korpus_schwarz", 1)], (0, 0))]),
    ("Sockel", [("Sockel", [("Sockel", "3_Sockel_schwarz", 1)], (0, 0))]),
    ("Bodenplatte", [("Bodenplatte", [("Bodenplatte", "4_Bodenplatte_schwarz", 1)], (0, 0))]),
    ("Klemmdeckel + Regendach", [
        ("Klemmdeckel", [("Klemmdeckel", "5_Klemmdeckel_schwarz", 1)], (0, -60.0)),
        ("Regendach", [("Regendach", "6_Regendach_schwarz", 1)], (0, 75.0)),
    ]),
]


def u():
    return str(uuid.uuid4())


def mesh_xml(oid, verts, faces):
    vs = "\n".join(f'     <vertex x="{x:.4f}" y="{y:.4f}" z="{z:.4f}"/>' for x, y, z in verts)
    ts = "\n".join(f'     <triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in faces)
    return (f'  <object id="{oid}" p:UUID="{u()}" type="model">\n   <mesh>\n    <vertices>\n{vs}\n    </vertices>\n'
            f'    <triangles>\n{ts}\n    </triangles>\n   </mesh>\n  </object>\n')


def build(out_path, plates=PLATES):
    files = {}
    main_objects, build_items, rels = [], [], []
    cfg_objects, cfg_plates = [], []
    next_id = 1
    sub_index = 0
    for pi, (pname, objs) in enumerate(plates):
        px, py = BED / 2, BED / 2   # jede Datei = eine Platte, mittig
        instances = []
        for oname, parts, (dx, dy) in objs:
            meshes = [(pn, g.to_trimesh(g.PARTS_PRINT[key]), ext) for pn, key, ext in parts]
            allv = np.vstack([m.vertices for _, m, _ in meshes])
            lo, hi = allv.min(0), allv.max(0)
            shift = np.array([-(lo[0] + hi[0]) / 2, -(lo[1] + hi[1]) / 2, -lo[2]])
            sub_index += 1
            sub_path = f"/3D/Objects/object_{sub_index}.model"
            sub_xml, comps, cfg_parts = [], [], []
            for pn, m, ext in meshes:
                mid = next_id; next_id += 1
                sub_xml.append(mesh_xml(mid, m.vertices + shift, m.faces))
                comps.append(f'    <component p:path="{sub_path}" objectid="{mid}" p:UUID="{u()}" transform="1 0 0 0 1 0 0 0 1 0 0 0"/>')
                cfg_parts.append(
                    f'    <part id="{mid}" subtype="normal_part">\n'
                    f'      <metadata key="name" value="{pn}"/>\n'
                    f'      <metadata key="matrix" value="1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"/>\n'
                    f'      <metadata key="extruder" value="{ext}"/>\n'
                    f'      <mesh_stat edges_fixed="0" degenerate_facets="0" facets_removed="0" facets_reversed="0" backwards_edges="0"/>\n'
                    f'    </part>')
            files[sub_path.lstrip("/")] = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<model unit="millimeter" xml:lang="en-US" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
                'xmlns:BambuStudio="http://schemas.bambulab.com/package/2021" '
                'xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06" requiredextensions="p">\n'
                ' <metadata name="BambuStudio:3mfVersion">1</metadata>\n <resources>\n'
                + "".join(sub_xml) + ' </resources>\n <build/>\n</model>\n')
            rels.append(sub_path)
            oid = next_id; next_id += 1
            main_objects.append(f'  <object id="{oid}" p:UUID="{u()}" type="model">\n   <components>\n'
                                + "\n".join(comps) + '\n   </components>\n  </object>')
            build_items.append(f'  <item objectid="{oid}" p:UUID="{u()}" '
                               f'transform="1 0 0 0 1 0 0 0 1 {px + dx:.3f} {py + dy:.3f} 0" printable="1"/>')
            first_ext = parts[0][2]
            cfg_objects.append(f'  <object id="{oid}">\n    <metadata key="name" value="{oname}"/>\n'
                               f'    <metadata key="extruder" value="{first_ext}"/>\n' + "\n".join(cfg_parts) + '\n  </object>')
            instances.append(f'    <model_instance>\n      <metadata key="object_id" value="{oid}"/>\n'
                             f'      <metadata key="instance_id" value="0"/>\n'
                             f'      <metadata key="identify_id" value="{oid * 10}"/>\n    </model_instance>')
        cfg_plates.append(f'  <plate>\n    <metadata key="plater_id" value="{pi + 1}"/>\n'
                          f'    <metadata key="plater_name" value="{pname}"/>\n'
                          f'    <metadata key="locked" value="false"/>\n' + "\n".join(instances) + '\n  </plate>')

    files["3D/3dmodel.model"] = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<model unit="millimeter" xml:lang="en-US" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
        'xmlns:BambuStudio="http://schemas.bambulab.com/package/2021" '
        'xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06" requiredextensions="p">\n'
        ' <metadata name="Application">BambuStudio-01.10.02.76</metadata>\n'
        ' <metadata name="BambuStudio:3mfVersion">1</metadata>\n'
        ' <metadata name="Title">Flüsternder Grabstein</metadata>\n'
        ' <metadata name="Designer">Benjamin Hartwich</metadata>\n'
        ' <metadata name="Description">Halloween-Gehäuse: Raspberry Pi 4, ReSpeaker XVF3800, HC-SR501. Extruder 1 schwarz, 2 weiß, 3 gelb/leuchtend, 4 rot.</metadata>\n'
        ' <resources>\n' + "\n".join(main_objects) + '\n </resources>\n'
        f' <build p:UUID="{u()}">\n' + "\n".join(build_items) + '\n </build>\n</model>\n')
    files["3D/_rels/3dmodel.model.rels"] = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
        + "\n".join(f' <Relationship Target="{r}" Id="rel-{i + 1}" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>'
                    for i, r in enumerate(rels)) + '\n</Relationships>\n')
    files["_rels/.rels"] = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
        ' <Relationship Target="/3D/3dmodel.model" Id="rel-1" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>\n'
        '</Relationships>\n')
    files["[Content_Types].xml"] = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
        ' <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
        ' <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>\n'
        ' <Default Extension="png" ContentType="image/png"/>\n'
        ' <Default Extension="config" ContentType="text/xml"/>\n'
        '</Types>\n')
    files["Metadata/model_settings.config"] = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<config>\n' + "\n".join(cfg_objects) + "\n"
        + "\n".join(cfg_plates) + '\n</config>\n')

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", files.pop("[Content_Types].xml"))
        z.writestr("_rels/.rels", files.pop("_rels/.rels"))
        for name, data in files.items():
            z.writestr(name, data)
        thumb = os.path.join(g.HERE, "vorschau_3d.png")
        if os.path.exists(thumb):
            z.write(thumb, "Metadata/plate_1.png")
    return out_path


FILENAMES = ["Druck_1_Frontplatte_4Farben.3mf", "Druck_2_Korpus.3mf", "Druck_3_Sockel.3mf", "Druck_4_Bodenplatte.3mf", "Druck_5_Klemmdeckel_Regendach.3mf"]

if __name__ == "__main__":
    outdir = os.path.join(g.HERE, "druck")
    os.makedirs(outdir, exist_ok=True)
    for plate, fn in zip(PLATES, FILENAMES):
        p = build(os.path.join(outdir, fn), [plate])
        print(p, os.path.getsize(p) // 1024, "KB")
