"""Geometry primitives -> polygon meshes (package Parts). All lengths in millimetres.

Coordinates are in the type's local system: Z up. Every primitive becomes a
mesh that the generator tessellates itself and the writer stores as an
IfcPolygonalFaceSet:
- round parts get a segment count from their diameter (see `segments_for`),
  so two parts of the same diameter match edge to edge at a joint;
- nothing depends on the project's angle unit;
- no IfcProfileDef entities, so Bonsai's Project Library lists only types.
"""

import math

import numpy as np

from .expr import ev, ExprError
from .package import Part

# Target facet length along a circle, mm. Library-wide setting.
DEFAULT_CHORD = 25.0
MIN_SEGMENTS = 16
MAX_SEGMENTS = 64


def _unit(v):
    v = np.asarray(v, dtype=float)
    n = np.linalg.norm(v)
    if n < 1e-9:
        raise ExprError("zero-length direction")
    return v / n


def _perp(a):
    """A unit vector perpendicular to a (stable choice, so equal axes give equal phase)."""
    a = _unit(a)
    ref = np.array([1.0, 0.0, 0.0]) if abs(a[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    p = np.cross(a, ref)
    p = np.cross(p, a)
    return _unit(p)


def _signed_area(pts):
    return 0.5 * sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1]
                     for i in range(len(pts)))


class Builder:
    def __init__(self, chord=None):
        self.chord = float(chord or DEFAULT_CHORD)

    # --- helpers -------------------------------------------------------------
    def segments_for(self, d):
        """Segments for a circle of diameter d: facet ~chord mm, multiple of 4, 16..64.
        Multiple of 4 puts vertices on the local axes; the first vertex is at 0°."""
        n = math.ceil(math.pi * float(d) / self.chord / 4) * 4
        return int(min(MAX_SEGMENTS, max(MIN_SEGMENTS, n)))

    def ngon(self, d, n=None):
        n = n or self.segments_for(d)
        r = float(d) / 2
        return [(r * math.cos(2 * math.pi * j / n), r * math.sin(2 * math.pi * j / n)) for j in range(n)]

    def faceset(self, coords, faces, closed=True):
        """coords: list of 3D points; faces: list of (outer index list, [hole index lists]), 0-based."""
        return Part([[float(c) for c in p] for p in coords], [(list(o), [list(h) for h in hs]) for o, hs in faces], closed)

    def prism(self, outer, holes, origin, z, x, depth):
        """Extrude a 2D polygon (with optional polygon holes) along local z by depth."""
        z = _unit(z)
        x = _unit(np.asarray(x, float) - np.dot(x, z) * z)
        y = np.cross(z, x)
        o = np.asarray(origin, float)
        outer = list(outer)
        if _signed_area(outer) < 0:
            outer.reverse()
        holes = [list(h) if _signed_area(h) < 0 else list(reversed(h)) for h in holes]
        loops = [outer] + holes
        coords, bottom, top = [], [], []
        for loop in loops:
            b, t = [], []
            for u, v in loop:
                p = o + u * x + v * y
                b.append(len(coords)); coords.append(p)
                t.append(len(coords)); coords.append(p + z * depth)
            bottom.append(b); top.append(t)
        faces = [
            (list(reversed(bottom[0])), [list(reversed(h)) for h in bottom[1:]]),
            (top[0], top[1:]),
        ]
        for b, t in zip(bottom, top):
            n = len(b)
            for i in range(n):
                j = (i + 1) % n
                faces.append(([b[i], b[j], t[j], t[i]], []))
        return self.faceset(coords, faces)

    # --- primitives ---------------------------------------------------------
    def _axis_frame(self, axis):
        if axis == "z":
            return (0, 0, 1), (1, 0, 0)
        if axis == "x":
            return (1, 0, 0), (0, 1, 0)
        if axis == "y":
            return (0, 1, 0), (1, 0, 0)
        raise ExprError(f"axis must be x, y or z, got {axis!r}")

    def _rect(self, u, v):
        return [(-u / 2, -v / 2), (u / 2, -v / 2), (u / 2, v / 2), (-u / 2, v / 2)]

    def _box_like(self, spec, env, hollow):
        sx, sy, sz = ev(spec["size"], env)
        c = np.array(ev(spec.get("center", [0, 0, 0]), env), float)
        axis = spec.get("axis", "z")
        zdir, xdir = self._axis_frame(axis)
        u, v, L = {"z": (sx, sy, sz), "x": (sy, sz, sx), "y": (sx, sz, sy)}[axis]
        start = c - np.array(zdir) * L / 2
        holes = []
        if hollow:
            wall = ev(spec["wall"], env)
            if wall * 2 < min(u, v):
                holes = [self._rect(u - 2 * wall, v - 2 * wall)]
        return [self.prism(self._rect(u, v), holes, start, zdir, xdir, L)]

    def box(self, spec, env):
        """box: {size: [x, y, z], center: [x, y, z]}"""
        return self._box_like(spec, env, hollow=False)

    def frame(self, spec, env):
        """frame: rectangular tube open along `axis`. {size, center, axis: x|y|z, wall}"""
        return self._box_like(spec, env, hollow=True)

    def _round(self, spec, env, wall):
        a = np.array(ev(spec["from"], env), float)
        b = np.array(ev(spec["to"], env), float)
        d = ev(spec["d"], env)
        n = int(ev(spec["segments"], env)) if "segments" in spec else self.segments_for(d)
        ax = b - a
        holes = [self.ngon(d - 2 * wall, n)] if wall else []
        return [self.prism(self.ngon(d, n), holes, a, ax, _perp(ax), np.linalg.norm(ax))]

    def cylinder(self, spec, env):
        """cylinder: {d, from: [x,y,z], to: [x,y,z]}"""
        return self._round(spec, env, 0)

    def tube(self, spec, env):
        """tube: hollow cylinder {d, wall, from, to}"""
        return self._round(spec, env, ev(spec["wall"], env))

    def plate(self, spec, env):
        """plate: horizontal plate, optionally with a round hole.
        {size: [x, y], thickness, center: [x, y, z] (middle of the plate), hole_d}"""
        sx, sy = ev(spec["size"], env)
        th = ev(spec["thickness"], env)
        c = np.array(ev(spec.get("center", [0, 0, 0]), env), float)
        hole = ev(spec.get("hole_d", 0), env)
        holes = [self.ngon(hole)] if hole and hole > 0 else []
        return [self.prism(self._rect(sx, sy), holes, c - np.array([0, 0, th / 2]), (0, 0, 1), (1, 0, 0), th)]

    def extrude_polygon(self, spec, env):
        """extrude_polygon: {points: [[u, v], ...], origin, normal, xdir, depth}"""
        pts = [tuple(ev(p, env)) for p in spec["points"]]
        o = ev(spec.get("origin", [0, 0, 0]), env)
        n = ev(spec.get("normal", [0, 0, 1]), env)
        x = ev(spec.get("xdir", [1, 0, 0]), env)
        return [self.prism(pts, [], o, n, x, ev(spec["depth"], env))]

    def revolve(self, spec, env):
        """revolve: closed profile [[r, h], ...] turned around an axis.
        {profile: [[r, h], ...], origin: [x,y,z], axis: [x,y,z], segments}
        r >= 0, h along the axis from origin. Segments default from the largest radius."""
        pts = [ev(p, env) for p in spec["profile"]]
        return [self._revolve_pts(pts, spec, env)]

    def _revolve_pts(self, pts, spec, env):
        o = np.array(ev(spec.get("origin", [0, 0, 0]), env), float)
        ax = _unit(ev(spec.get("axis", [0, 0, 1]), env))
        rmax = max(r for r, _ in pts)
        n = int(ev(spec["segments"], env)) if "segments" in spec else self.segments_for(2 * rmax)
        lx = _perp(ax)
        ly = np.cross(ax, lx)
        coords, index = [], []
        for r, h in pts:
            if r < 1e-6:
                index.append([len(coords)] * n)
                coords.append(o + ax * h)
            else:
                row = []
                for j in range(n):
                    t = 2 * math.pi * j / n
                    row.append(len(coords))
                    coords.append(o + ax * h + r * (math.cos(t) * lx + math.sin(t) * ly))
                index.append(row)
        faces = []
        m = len(pts)
        for i in range(m):
            a, b = index[i], index[(i + 1) % m]
            for j in range(n):
                k = (j + 1) % n
                face = []
                for q in (a[j], a[k], b[k], b[j]):
                    if q not in face:
                        face.append(q)
                if len(face) >= 3:
                    faces.append((face, []))
        return self.faceset(coords, faces)

    def arc_ring(self, spec, env):
        """arc_ring: thin shell of revolution between two points of its section.

        {r1, z1, r2, z2, wall, shape: quarter|straight, steps: 6, origin, axis}
        quarter: leaves (r1, z1) along the radius (horizontal tangent) and arrives
        at (r2, z2) along the axis (vertical tangent) — a curved diffuser vane.
        straight: a conical shell. The wall is offset to the inside of the curve.
        """
        r1, z1, r2, z2 = (ev(spec[k], env) for k in ("r1", "z1", "r2", "z2"))
        wall = ev(spec.get("wall", 1), env)
        shape = spec.get("shape", "quarter")
        steps = int(ev(spec.get("steps", 6), env)) if shape == "quarter" else 1
        line = []
        for s in range(steps + 1):
            t = (math.pi / 2) * s / steps if shape == "quarter" else s
            if shape == "quarter":
                line.append((r1 + (r2 - r1) * math.sin(t), z1 + (z2 - z1) * (1 - math.cos(t))))
            else:
                line.append((r1 + (r2 - r1) * t, z1 + (z2 - z1) * t))
        # offset by the wall along the local normal (towards the axis side / up)
        off = []
        for i, (r, z) in enumerate(line):
            a = line[max(i - 1, 0)]
            b = line[min(i + 1, len(line) - 1)]
            dr, dz = b[0] - a[0], b[1] - a[1]
            ln = math.hypot(dr, dz) or 1.0
            nr, nz = -dz / ln, dr / ln
            if nz < 0 or (abs(nz) < 1e-9 and nr > 0):
                nr, nz = -nr, -nz
            off.append((max(r + nr * wall, 0.0), z + nz * wall))
        return [self._revolve_pts(line + list(reversed(off)), spec, env)]

    def snap_point(self, p):
        """A 0.1 mm triangle: in practice a vertex to snap to (IFC bodies cannot hold bare points)."""
        p = np.asarray(p, float)
        e = 0.05
        coords = [p + np.array([e * math.cos(a), e * math.sin(a), 0.0]) for a in (0, 2 * math.pi / 3, 4 * math.pi / 3)]
        return self.faceset(coords, [([0, 1, 2], [])], closed=False)

    PRIMITIVES = ("box", "frame", "plate", "cylinder", "tube", "revolve", "arc_ring", "extrude_polygon")

    def build(self, item, env):
        kinds = [k for k in self.PRIMITIVES if k in item]
        if len(kinds) != 1:
            raise ExprError(f"geometry item needs exactly one of {self.PRIMITIVES}: {item}")
        kind = kinds[0]
        return getattr(self, kind)(item[kind], env), item.get("style")
