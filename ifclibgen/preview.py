"""Contact sheet of selected types: shaded body + ports as red arrows.

python -m ifclibgen.preview out/example-dampers.ifc out/preview.png "Example damper 600x400" ...
"""

import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from mpl_toolkits.mplot3d.art3d import Poly3DCollection

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.placement
import ifcopenshell.util.system

LIGHT = np.array([0.4, -0.6, 0.7]) / np.linalg.norm([0.4, -0.6, 0.7])


def draw(ax, f, t, settings):
    shape = ifcopenshell.geom.create_shape(settings, t.RepresentationMaps[0].MappedRepresentation)
    v = np.array(shape.verts, float).reshape(-1, 3)
    tri = np.array(shape.faces, int).reshape(-1, 3)
    mats = list(shape.materials)
    mids = list(shape.material_ids)
    polys = v[tri]
    n = np.cross(polys[:, 1] - polys[:, 0], polys[:, 2] - polys[:, 0])
    n /= np.linalg.norm(n, axis=1, keepdims=True) + 1e-12
    shade = 0.45 + 0.55 * np.clip(np.abs(n @ LIGHT), 0, 1)
    cols = []
    for i in range(len(tri)):
        m = mats[mids[i]] if mids and mids[i] >= 0 else None
        base = np.array(m.diffuse.components if m is not None else (0.8, 0.8, 0.8))[:3]
        cols.append(np.clip(base * shade[i], 0, 1))
    ax.add_collection3d(Poly3DCollection(polys, facecolors=cols, edgecolors="none", linewidths=0))
    lo, hi = v.min(0), v.max(0)
    size = (hi - lo).max()
    for port in ifcopenshell.util.system.get_ports(t):
        m = ifcopenshell.util.placement.get_local_placement(port.ObjectPlacement)
        p, d = m[:3, 3], m[:3, 2]
        ax.quiver(*p, *(d * size * 0.35), color="red", linewidth=2, arrow_length_ratio=0.3)
        ax.scatter(*p, color="red", s=12)
    c = (lo + hi) / 2
    r = size * 0.6
    ax.set_xlim(c[0] - r, c[0] + r)
    ax.set_ylim(c[1] - r, c[1] + r)
    ax.set_zlim(c[2] - r, c[2] + r)
    ax.set_box_aspect((1, 1, 1))
    ax.view_init(elev=float(__import__("os").environ.get("ELEV", 22)), azim=-55)
    ax.set_axis_off()
    ax.set_title(t.Name, fontsize=9)


def sheet(ifc_path, out_png, names, cols=4):
    f = ifcopenshell.open(ifc_path)
    by_name = {t.Name: t for t in f.by_type("IfcTypeProduct")}
    picked = []
    for n in names:
        t = by_name.get(n)
        if t is None:
            raise SystemExit(f"type not found: {n}")
        picked.append(t)
    settings = ifcopenshell.geom.settings()
    settings.set("convert-back-units", True)
    rows = (len(picked) + cols - 1) // cols
    fig = plt.figure(figsize=(cols * 3.2, rows * 3.2), dpi=110)
    for i, t in enumerate(picked):
        ax = fig.add_subplot(rows, cols, i + 1, projection="3d")
        draw(ax, f, t, settings)
    fig.tight_layout()
    fig.savefig(out_png, facecolor="white")


if __name__ == "__main__":
    sheet(sys.argv[1], sys.argv[2], sys.argv[3:])
