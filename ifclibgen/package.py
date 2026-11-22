"""Size package: the exchange format between authoring tools and the IFC writer.

A package is everything needed to write a family's types, with the geometry
already computed, so any tool can produce one: YAML templates (expanded
in memory), the FreeCAD workbench (one row of its size table per type), the
Blender addon (one mesh, one type).

On disk a package is a folder:

    package.yaml          manifest (see below)
    meshes/<file>.json    one mesh per type (or .obj)

package.yaml:

    format: ifclibgen-package/1
    source: acme/damper               # stable id of the family; GlobalIds come from source + type name
    manufacturer: Acme
    group: acme-dampers               # library file the types go to
    ifc: {class: IfcDamperType, predefined: CONTROLDAMPER, element_type: ...}
    styles: {galv: {rgb: [0.72, 0.74, 0.76], transparency: 0}}
    types:
      - name: Acme damper 600x400
        description: ...
        insertion: центр входа, ось по +X     # text, goes to the description
        element_type: ...                     # optional, overrides ifc.element_type
        params: {E: 600, B: 400}              # numbers, go to LibGen_Parameters
        mesh: meshes/acme-damper-600x400.json
        ports:
          - {name: Inlet, at: [0, 0, 0], dir: [-1, 0, 0], xdir: [0, 1, 0],
             shape: rect, w: 600, h: 400, flow: SINK, system: supply, connection: ...}
        points: [{name: Top, at: [61, 0, 200]}]   # extra snap points
        psets: {Pset_DamperTypeCommon: {Operation: MANUAL, NumberofBlades: 4}}

All lengths in millimetres, Z up, in the type's own coordinate system.

Mesh JSON: {"parts": [{"style": "galv", "closed": true,
                       "coords": [[x, y, z], ...],
                       "faces": [[i, j, k, ...], {"outer": [...], "holes": [[...]]}, ...]}]}
Indices are 0-based. A face is a plain index list, or outer loop + holes.
OBJ is accepted too: `o`/`g` start a part, `usemtl` sets its style.
"""

import hashlib
import json
import os
import re
from dataclasses import dataclass, field

try:
    import yaml  # optional: Blender has no PyYAML, JSON manifests work without it
except ImportError:
    yaml = None

FORMAT = "ifclibgen-package/1"


@dataclass
class Part:
    """One polygon mesh: coords (list of xyz), faces (list of (outer, holes)), 0-based."""
    coords: list
    faces: list
    closed: bool = True
    style: str = None


@dataclass
class TypeDef:
    name: str
    parts: list
    description: str = ""
    insertion: str = ""
    element_type: str = ""  # overrides ifc.element_type for this type
    params: dict = field(default_factory=dict)
    ports: list = field(default_factory=list)
    points: list = field(default_factory=list)
    psets: dict = field(default_factory=dict)
    mesh_file: str = ""  # where the mesh was loaded from / will be saved to


@dataclass
class Package:
    source: str
    ifc: dict
    manufacturer: str = ""
    group: str = ""
    styles: dict = field(default_factory=dict)
    types: list = field(default_factory=list)
    path: str = ""  # folder on disk, if loaded from one


# --------------------------------------------------------------------------
# hashing


def _r(v):
    if isinstance(v, float):
        return round(v, 3)
    if isinstance(v, (list, tuple)):
        return [_r(x) for x in v]
    if isinstance(v, dict):
        return {k: _r(x) for k, x in sorted(v.items())}
    return v


def content_hash(pkg, t):
    """Hash of everything that ends up in the IFC type: lets an update skip unchanged types."""
    data = {
        "ifc": pkg.ifc, "manufacturer": pkg.manufacturer, "name": t.name,
        "description": t.description, "insertion": t.insertion, "element_type": t.element_type,
        "params": t.params, "ports": t.ports, "points": t.points, "psets": t.psets,
        "styles": {p.style: pkg.styles.get(p.style) for p in t.parts if p.style},
        "parts": [[p.style, p.closed, p.coords, [[list(o), [list(h) for h in hs]] for o, hs in p.faces]]
                  for p in t.parts],
    }
    blob = json.dumps(_r(data), sort_keys=True, ensure_ascii=False, default=float)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------
# meshes


def _face_out(face):
    outer, holes = face
    return list(outer) if not holes else {"outer": list(outer), "holes": [list(h) for h in holes]}


def _face_in(face):
    if isinstance(face, dict):
        return (list(face["outer"]), [list(h) for h in face.get("holes", [])])
    return (list(face), [])


def save_mesh_json(parts, path):
    data = {"parts": [{"style": p.style, "closed": p.closed,
                       "coords": [[round(float(c), 4) for c in xyz] for xyz in p.coords],
                       "faces": [_face_out(f) for f in p.faces]} for p in parts]}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, separators=(",", ":"))


def load_mesh_json(path):
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    return [Part([list(map(float, c)) for c in p["coords"]], [_face_in(f) for f in p["faces"]],
                 p.get("closed", True), p.get("style")) for p in data["parts"]]


def load_mesh_obj(path):
    """Minimal OBJ: v, f (with v/vt/vn forms), o/g start a new part, usemtl sets style."""
    verts, parts = [], []
    cur = None

    def new_part(style=None):
        nonlocal cur
        cur = {"faces": [], "style": style}
        parts.append(cur)

    with open(path, encoding="utf-8") as fh:
        for line in fh:
            tok = line.split()
            if not tok:
                continue
            if tok[0] == "v":
                verts.append([float(x) for x in tok[1:4]])
            elif tok[0] in ("o", "g"):
                new_part(cur["style"] if cur else None)
            elif tok[0] == "usemtl":
                if cur is None or cur["faces"]:
                    new_part(tok[1])
                else:
                    cur["style"] = tok[1]
            elif tok[0] == "f":
                if cur is None:
                    new_part()
                idx = []
                for v in tok[1:]:
                    i = int(v.split("/")[0])
                    idx.append(i - 1 if i > 0 else len(verts) + i)
                cur["faces"].append(idx)
    out = []
    for p in parts:
        if not p["faces"]:
            continue
        used = sorted({i for f in p["faces"] for i in f})
        remap = {g: k for k, g in enumerate(used)}
        out.append(Part([verts[g] for g in used], [([remap[i] for i in f], []) for f in p["faces"]], True, p["style"]))
    return out


def load_mesh(path):
    if path.lower().endswith(".obj"):
        return load_mesh_obj(path)
    return load_mesh_json(path)


# --------------------------------------------------------------------------
# package folders


def _slug(s):
    s = re.sub(r"[^\w.-]+", "-", s, flags=re.UNICODE).strip("-")
    return s or "type"


def save_package(pkg, folder):
    os.makedirs(os.path.join(folder, "meshes"), exist_ok=True)
    types = []
    used = set()
    for t in pkg.types:
        mf = t.mesh_file
        if not mf:
            base = _slug(t.name)
            mf = f"meshes/{base}.json"
            n = 2
            while mf in used:
                mf = f"meshes/{base}-{n}.json"
                n += 1
        used.add(mf)
        save_mesh_json(t.parts, os.path.join(folder, mf))
        entry = {"name": t.name}
        for k in ("description", "insertion", "element_type"):
            if getattr(t, k):
                entry[k] = getattr(t, k)
        if t.params:
            entry["params"] = t.params
        entry["mesh"] = mf
        for k in ("ports", "points", "psets"):
            if getattr(t, k):
                entry[k] = getattr(t, k)
        types.append(entry)
    manifest = {"format": FORMAT, "source": pkg.source, "manufacturer": pkg.manufacturer,
                "group": pkg.group, "ifc": pkg.ifc, "styles": pkg.styles, "types": types}
    with open(os.path.join(folder, "package.yaml"), "w", encoding="utf-8") as fh:
        if yaml is not None:
            yaml.safe_dump(manifest, fh, allow_unicode=True, sort_keys=False, default_flow_style=None, width=120)
        else:  # JSON is valid YAML
            json.dump(manifest, fh, ensure_ascii=False, indent=1)


def load_package(folder):
    with open(os.path.join(folder, "package.yaml"), encoding="utf-8") as fh:
        text = fh.read()
    try:
        m = json.loads(text)
    except ValueError:
        if yaml is None:
            raise ValueError(f"{folder}: package.yaml is not JSON and PyYAML is not installed") from None
        m = yaml.safe_load(text)
    fmt = m.get("format", FORMAT)
    if not str(fmt).startswith("ifclibgen-package/"):
        raise ValueError(f"{folder}: not an ifclibgen package (format: {fmt})")
    pkg = Package(m["source"], m["ifc"], m.get("manufacturer", ""), m.get("group", ""), m.get("styles") or {}, [], folder)
    for e in m.get("types") or []:
        parts = load_mesh(os.path.join(folder, e["mesh"]))
        pkg.types.append(TypeDef(e["name"], parts, e.get("description", ""), e.get("insertion", ""),
                                 e.get("element_type", ""), e.get("params") or {}, e.get("ports") or [], e.get("points") or [],
                                 e.get("psets") or {}, e["mesh"]))
    return pkg


def find_packages(root):
    """Package folders under root (a folder with package.yaml), or root itself."""
    if os.path.isfile(os.path.join(root, "package.yaml")):
        return [root]
    out = []
    for dirpath, dirs, files in os.walk(root):
        if "package.yaml" in files:
            out.append(dirpath)
            dirs[:] = []
    return sorted(out)
