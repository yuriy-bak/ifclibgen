"""Checks for a generated library: schema, geometry, ports."""

import numpy as np

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.placement
import ifcopenshell.util.system
import ifcopenshell.validate


def type_mesh(settings, t):
    """Triangulated body of a type's first representation map, in mm."""
    maps = t.RepresentationMaps or []
    if not maps:
        return None, None
    shape = ifcopenshell.geom.create_shape(settings, maps[0].MappedRepresentation)
    v = np.array(shape.verts, dtype=float).reshape(-1, 3)
    faces = np.array(shape.faces, dtype=int).reshape(-1, 3)
    return v, faces


def settings_mm():
    s = ifcopenshell.geom.settings()
    s.set("convert-back-units", True)  # keep file units (mm)
    return s


def check_file(path, tol=1.0):
    f = ifcopenshell.open(path)
    problems = []

    class Log:
        def __init__(self):
            self.statements = []

        def error(self, msg, *args):
            self.statements.append(msg % args if args else msg)

        warning = info = debug = error

        def __getattr__(self, name):
            return lambda *a, **k: None

    log = Log()
    ifcopenshell.validate.validate(f, log)
    problems += [f"schema: {s}" for s in log.statements]

    settings = settings_mm()
    for t in f.by_type("IfcTypeProduct"):
        try:
            v, _ = type_mesh(settings, t)
        except Exception as e:  # geometry kernel failure
            problems.append(f"{t.Name}: geometry failed: {e}")
            continue
        if v is None or len(v) == 0:
            problems.append(f"{t.Name}: no geometry")
            continue
        lo, hi = v.min(0), v.max(0)
        for port in ifcopenshell.util.system.get_ports(t):
            m = ifcopenshell.util.placement.get_local_placement(port.ObjectPlacement)
            p, d = m[:3, 3], m[:3, 2]
            # port must sit on the bounding box and point outwards
            out_of_box = np.maximum(lo - p, 0) + np.maximum(p - hi, 0)
            if np.linalg.norm(out_of_box) > tol:
                problems.append(f"{t.Name}: port {port.Name} is {np.linalg.norm(out_of_box):.1f} mm outside the body")
                continue
            ax = int(np.argmax(np.abs(d)))
            face = hi[ax] if d[ax] > 0 else lo[ax]
            if abs(p[ax] - face) > tol:
                problems.append(f"{t.Name}: port {port.Name} is not on the outer face it points to "
                                f"({abs(p[ax] - face):.1f} mm off)")
    return problems
