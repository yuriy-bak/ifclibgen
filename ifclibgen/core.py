"""Library generator core.

Templates + size data are expanded into packages (package.py); packages from
any source (templates, FreeCAD, Blender, folders on disk) are written to IFC
library files, one per group.
"""

import csv
import itertools
import os
import uuid
from dataclasses import dataclass, field

import ifcopenshell
import ifcopenshell.api.context
import ifcopenshell.api.geometry
import ifcopenshell.api.project
import ifcopenshell.api.pset
import ifcopenshell.api.root
import ifcopenshell.api.unit
import ifcopenshell.guid

from .expr import ev, fmt, value, ExprError
from .geometry import Builder
from .package import Package, TypeDef, content_hash, find_packages, load_package, save_package

# Fixed namespace: the same manufacturer/model/size always gives the same GlobalId,
# so regenerating the library updates types instead of duplicating them.
NAMESPACE = uuid.UUID("6f1c2a52-3c1e-4b8e-9a4f-1f0d6c0b7e21")


def stable_guid(*parts):
    return ifcopenshell.guid.compress(uuid.uuid5(NAMESPACE, "|".join(parts)).hex)


# --------------------------------------------------------------------------
# loading


@dataclass
class Template:
    id: str
    path: str
    spec: dict

    @classmethod
    def load(cls, path, root):
        import yaml
        with open(path, encoding="utf-8") as fh:
            spec = yaml.safe_load(fh)
        tid = spec.get("id") or os.path.splitext(os.path.relpath(path, root))[0].replace(os.sep, "/")
        return cls(tid, path, spec)


@dataclass
class SizeSet:
    template: str
    rows: list = field(default_factory=list)
    source: str = ""
    group: str = ""


def load_templates(root):
    out = {}
    for dirpath, _, files in os.walk(root):
        for fn in sorted(files):
            if fn.endswith((".yaml", ".yml")):
                t = Template.load(os.path.join(dirpath, fn), root)
                out[t.id] = t
    return out


def _expand_set(s, base_dir):
    """One size set -> list of parameter dicts.

    table (CSV file) and rows (inline list) give base rows; grid gives the
    cartesian product of lists and is combined with every base row. fixed adds
    constants, map adds computed columns (expressions), where filters rows.
    """
    fixed = dict(s.get("fixed", {}))
    base = []
    if "table" in s:
        with open(os.path.join(base_dir, s["table"]), encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                base.append({k.strip(): _num(v) for k, v in r.items() if k and v not in (None, "")})
    if "rows" in s:
        base.extend(dict(r) for r in s["rows"])
    base = base or [{}]
    combos = [{}]
    if "grid" in s:
        keys = list(s["grid"])
        combos = [dict(zip(keys, c)) for c in itertools.product(*(s["grid"][k] for k in keys))]
    out = []
    for b in base:
        for c in combos:
            p = {**fixed, **b, **c}
            for k, expr in (s.get("map") or {}).items():
                p[k] = ev(expr, p)
            if "where" in s and not ev(s["where"], p):
                continue
            out.append(p)
    return out


def _num(v):
    v = v.strip()
    try:
        return int(v)
    except ValueError:
        try:
            return float(v.replace(",", "."))
        except ValueError:
            return v


def load_size_sets(root):
    out = []
    for dirpath, _, files in os.walk(root):
        for fn in sorted(files):
            if not fn.endswith((".yaml", ".yml")):
                continue
            import yaml
            with open(os.path.join(dirpath, fn), encoding="utf-8") as fh:
                spec = yaml.safe_load(fh)
            sets = spec.get("sets") or [spec]
            rows = []
            for s in sets:
                rows.extend(_expand_set(s, dirpath))
            rel = os.path.relpath(os.path.join(dirpath, fn), root)
            group = spec.get("file") or os.path.splitext(rel)[0].replace(os.sep, "-")
            out.append(SizeSet(spec["template"], rows, rel, group))
    return out


# --------------------------------------------------------------------------
# library file


class Library:
    def __init__(self, name="Library"):
        f = ifcopenshell.api.project.create_file(version="IFC4")
        self.f = f
        self.project = ifcopenshell.api.root.create_entity(f, ifc_class="IfcProject", name=name)
        units = [
            ifcopenshell.api.unit.add_si_unit(f, unit_type="LENGTHUNIT", prefix="MILLI"),
            ifcopenshell.api.unit.add_si_unit(f, unit_type="AREAUNIT"),
            ifcopenshell.api.unit.add_si_unit(f, unit_type="VOLUMEUNIT"),
            ifcopenshell.api.unit.add_si_unit(f, unit_type="PLANEANGLEUNIT"),
        ]
        ifcopenshell.api.unit.assign_unit(f, units=units)
        model = ifcopenshell.api.context.add_context(f, context_type="Model")
        self.body = ifcopenshell.api.context.add_context(
            f, context_type="Model", context_identifier="Body", target_view="MODEL_VIEW", parent=model
        )
        self.library = ifcopenshell.api.root.create_entity(f, ifc_class="IfcProjectLibrary", name=name)
        ifcopenshell.api.project.assign_declaration(f, definitions=[self.library], relating_context=self.project)
        self._styles = {}
        self.types = []

    # styles are shared per (template, name)
    def style(self, name, rgb, transparency=0.0):
        key = (name, tuple(rgb), transparency)
        st = self._styles.get(key)
        if st is None:
            f = self.f
            colour = f.createIfcColourRgb(None, *[float(c) for c in rgb])
            rendering = f.createIfcSurfaceStyleRendering(colour, float(transparency), None, None, None, None, None, None, "NOTDEFINED")
            st = f.createIfcSurfaceStyle(name, "BOTH", [rendering])
            self._styles[key] = st
        return st

    def write(self, path):
        if self.types:
            ifcopenshell.api.project.assign_declaration(self.f, definitions=self.types, relating_context=self.library)
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.f.write(path)


# --------------------------------------------------------------------------
# templates -> packages


def _iter_items(items, env):
    """Expand `when` and `repeat` on geometry/port items."""
    for item in items or []:
        if "when" in item and not ev(item["when"], env):
            continue
        rep = item.get("repeat")
        if rep:
            var = rep.get("var", "i")
            for i in range(int(ev(rep["count"], env))):
                yield item, {**env, var: i}
        else:
            yield item, env


def resolve_params(t, row):
    spec = t.spec
    env = dict(spec.get("constants") or {})
    for k, decl in (spec.get("params") or {}).items():
        if isinstance(decl, dict) and "default" in decl:
            env[k] = decl["default"]
    env.update(row)
    missing = [k for k in (spec.get("params") or {}) if k not in env]
    if missing:
        raise ExprError(f"{t.id}: missing parameters {missing} in row {row}")
    for k, expr in (spec.get("derived") or {}).items():
        env[k] = ev(expr, env)
    return env


def _num_list(v):
    return [float(c) for c in v]


def template_package(t, rows, group="", chord=None):
    """Expand a template over size rows into a Package (geometry computed)."""
    spec = t.spec
    ifc = {k: v for k, v in spec["ifc"].items()}
    pkg = Package(t.id, ifc, spec.get("manufacturer", ""), group, dict(spec.get("styles") or {}))
    geo = Builder(chord)
    default_style = spec.get("default_style")
    consts = spec.get("constants") or {}
    for row in rows:
        env = resolve_params(t, row)
        name = fmt(spec["name"], env)
        parts = []
        for n, (item, ienv) in enumerate(_iter_items(spec.get("geometry"), env)):
            try:
                built, sname = geo.build(item, ienv)
            except ExprError as e:
                raise ExprError(f"{t.id}: {name}: geometry item {n + 1} {list(item)[0]}: {e}") from None
            for p in built:
                p.style = sname or default_style
                parts.append(p)
        if not parts:
            raise ExprError(f"{t.id}: {name} has no geometry")
        ports = []
        for item, ienv in _iter_items(spec.get("ports"), env):
            try:
                ports.append(_port_def(item, ienv))
            except ExprError as e:
                raise ExprError(f"{t.id}: {name}: port {item.get('name')}: {e}") from None
        points = [{"name": fmt(item.get("name", ""), ienv), "at": _num_list(ev(item["at"], ienv))}
                  for item, ienv in _iter_items(spec.get("points"), env)]
        psets = {}
        for pname, props in (spec.get("psets") or {}).items():
            vals = {k: value(v, env) for k, v in props.items()}
            psets[pname] = {k: v for k, v in vals.items() if v not in (None, "")}
        params = {k: v for k, v in env.items()
                  if k not in consts and isinstance(v, (int, float)) and not isinstance(v, bool)}
        pkg.types.append(TypeDef(
            name, parts,
            fmt(spec["description"], env) if spec.get("description") else "",
            fmt(spec["insertion"], env) if spec.get("insertion") else "",
            fmt(ifc["element_type"], env) if ifc.get("element_type") else "",
            params, ports, points, psets))
    return pkg


def _port_def(item, env):
    d = {"name": fmt(item.get("name", "Port"), env), "at": _num_list(ev(item["at"], env)),
         "dir": _num_list(ev(item["dir"], env))}
    if "xdir" in item:
        d["xdir"] = _num_list(ev(item["xdir"], env))
    shape = item.get("shape", "round")
    d["shape"] = shape
    if shape == "round":
        d["d"] = float(ev(item["d"], env))
    else:
        d["w"] = float(ev(item["w"], env))
        d["h"] = float(ev(item["h"], env))
    for k in ("flow", "system", "connection", "predefined"):
        if item.get(k):
            d[k] = item[k]
    return d


# --------------------------------------------------------------------------
# packages -> IFC


def _place(f, loc, z=(0, 0, 1), x=None):
    import numpy as np
    from .geometry import _perp, _unit
    z = _unit(z)
    x = _perp(z) if x is None else _unit(np.asarray(x, float) - np.dot(x, z) * z)
    return f.createIfcAxis2Placement3D(
        f.createIfcCartesianPoint([float(c) for c in loc]),
        f.createIfcDirection([float(c) for c in z]),
        f.createIfcDirection([float(c) for c in x]),
    )


def _faceset(f, part):
    items = []
    for outer, holes in part.faces:
        o = [i + 1 for i in outer]
        if holes:
            items.append(f.createIfcIndexedPolygonalFaceWithVoids(o, [[i + 1 for i in h] for h in holes]))
        else:
            items.append(f.createIfcIndexedPolygonalFace(o))
    plist = f.createIfcCartesianPointList3D([[round(float(c), 4) for c in p] for p in part.coords])
    return f.createIfcPolygonalFaceSet(plist, bool(part.closed), items, None)


def _snap_part(p):
    """A 0.1 mm triangle: in practice a vertex to snap to (IFC bodies cannot hold bare points)."""
    import math
    e = 0.05
    coords = [[p[0] + e * math.cos(a), p[1] + e * math.sin(a), p[2]] for a in (0, 2 * math.pi / 3, 4 * math.pi / 3)]
    from .package import Part
    return Part(coords, [([0, 1, 2], [])], False)


def write_type(lib, pkg, t):
    f = lib.f
    ifc = pkg.ifc
    elem = ifcopenshell.api.root.create_entity(f, ifc_class=ifc["class"], predefined_type=ifc.get("predefined"), name=t.name)
    elem.GlobalId = stable_guid(pkg.source, t.name)
    et = t.element_type or ifc.get("element_type")
    if et:
        elem.ElementType = et
    desc = t.description or ""
    if t.insertion:
        desc = (desc + ". " if desc else "") + "Точка установки: " + t.insertion
    if desc:
        elem.Description = desc

    # body + snap points (insertion point, every port, extra points)
    default_style = next((p.style for p in t.parts if p.style), None)
    parts = list(t.parts)
    seen = set()
    for p in [[0, 0, 0]] + [pt["at"] for pt in t.ports] + [pt["at"] for pt in t.points]:
        key = tuple(round(float(c), 3) for c in p)
        if key not in seen:
            seen.add(key)
            sp = _snap_part([float(c) for c in p])
            sp.style = default_style
            parts.append(sp)
    items = []
    for part in parts:
        s = _faceset(f, part)
        if part.style:
            sdef = pkg.styles.get(part.style) or {}
            st = lib.style(f"{pkg.manufacturer} {part.style}".strip(), sdef.get("rgb", [0.8, 0.8, 0.8]), sdef.get("transparency", 0.0))
            f.createIfcStyledItem(s, [st], None)
        items.append(s)
    rep = f.createIfcShapeRepresentation(lib.body, "Body", "Tessellation", items)
    ifcopenshell.api.geometry.assign_representation(f, product=elem, representation=rep)

    # ports, nested in the type; Bonsai copies them to each new occurrence
    ports = [write_port(lib, elem, p) for p in t.ports]
    if ports:
        f.createIfcRelNests(stable_guid(elem.GlobalId, "nests"), None, None, None, elem, ports)

    for pname, vals in t.psets.items():
        pset = ifcopenshell.api.pset.add_pset(f, product=elem, name=pname)
        ifcopenshell.api.pset.edit_pset(f, pset=pset, properties=dict(vals))
    params = {k: float(v) for k, v in t.params.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
    pset = ifcopenshell.api.pset.add_pset(f, product=elem, name="LibGen_Parameters")
    ifcopenshell.api.pset.edit_pset(f, pset=pset, properties={
        "Source": pkg.source, "ContentHash": content_hash(pkg, t), **params})
    lib.types.append(elem)
    return elem


SYSTEMS = {"supply": "VENTILATION", "return": "VENTILATION", "exhaust": "EXHAUST", "air": "AIRCONDITIONING"}


def write_port(lib, elem, d):
    f = lib.f
    pname = d.get("name", "Port")
    placement = f.createIfcLocalPlacement(None, _place(f, d["at"], d["dir"], d.get("xdir")))
    system = d.get("system", "air")
    port = f.createIfcDistributionPort(
        stable_guid(elem.GlobalId, "port", pname), None, pname, None, None, placement, None,
        d.get("flow", "SOURCEANDSINK"), d.get("predefined", "DUCT"), SYSTEMS.get(system, system),
    )
    props = {}
    if d.get("shape", "round") == "round":
        props["NominalWidth"] = float(d["d"])
        props["ConnectionSubType"] = "ROUND"
    else:
        props["NominalWidth"] = float(d["w"])
        props["NominalHeight"] = float(d["h"])
        props["ConnectionSubType"] = "RECTANGULAR"
    if d.get("connection"):
        props["ConnectionType"] = d["connection"]
    pset = ifcopenshell.api.pset.add_pset(f, product=port, name="Pset_DistributionPortTypeDuct")
    ifcopenshell.api.pset.edit_pset(f, pset=pset, properties=props)
    return port


# --------------------------------------------------------------------------
# entry points


def template_packages(template_root, data_root, only=None, chord=None):
    """All size sets expanded into packages. Returns [(package, source label)]."""
    templates = load_templates(template_root)
    out = []
    for s in load_size_sets(data_root):
        if only and s.template not in only:
            continue
        t = templates.get(s.template)
        if t is None:
            raise ExprError(f"size data refers to unknown template {s.template!r}")
        out.append((template_package(t, s.rows, s.group, chord), s.source))
    return out


def write_packages(packages, out_path, name="Library", split=False):
    """Write packages to one file, or with split=True one file per group in the out_path folder."""
    libs = {}
    names = {}
    for pkg in packages:
        key = (pkg.group or "library") if split else ""
        if key not in libs:
            libs[key] = Library(f"{name} — {key}" if key else name)
        lib = libs[key]
        for t in pkg.types:
            other = names.get((key, t.name))
            if other is not None:
                raise ExprError(f"{pkg.source}: duplicate type name {t.name!r} (also in {other}); "
                                "add the varying parameter to the name")
            names[(key, t.name)] = pkg.source
            write_type(lib, pkg, t)
    written = []
    for key, lib in libs.items():
        path = os.path.join(out_path, f"{key}.ifc") if split else out_path
        lib.write(path)
        written.append((path, len(lib.types)))
    return written


def build_library(template_root, data_root, out_path, only=None, name="Library", split=False, chord=None,
                  package_roots=()):
    """Templates + data, plus package folders, into library file(s)."""
    pkgs = []
    report = []
    if data_root:
        for pkg, src in template_packages(template_root, data_root, only, chord):
            pkgs.append(pkg)
            report.append((src, len(pkg.types)))
    for root in package_roots or ():
        for folder in find_packages(root):
            pkg = load_package(folder)
            if only and pkg.source not in only:
                continue
            pkgs.append(pkg)
            report.append((folder, len(pkg.types)))
    written = write_packages(pkgs, out_path, name, split)
    return written, report


def export_packages(template_root, data_root, out_dir, only=None, chord=None):
    """Templates + data saved as package folders (one per data file)."""
    written = []
    for pkg, src in template_packages(template_root, data_root, only, chord):
        folder = os.path.join(out_dir, os.path.splitext(src)[0])
        save_package(pkg, folder)
        written.append((folder, len(pkg.types)))
    return written


def rebuild_group(library_dir, group, name="Library"):
    """Library folder layout: packages under <library_dir>/packages/, built files
    <library_dir>/<group>.ifc. Rebuild one group file from all its packages."""
    pkgs = [p for p in (load_package(f) for f in find_packages(os.path.join(library_dir, "packages"))) if p.group == group]
    if not pkgs:
        raise ExprError(f"no packages of group {group!r} in {library_dir}")
    path = os.path.join(library_dir, f"{group}.ifc")
    write_packages(pkgs, path, f"{name} — {group}")
    return path, sum(len(p.types) for p in pkgs)


def build_one(template_root, template_id, params, out_path, name="Library"):
    templates = load_templates(template_root)
    pkg = template_package(templates[template_id], [params])
    lib = Library(name)
    elem = write_type(lib, pkg, pkg.types[0])
    lib.write(out_path)
    return lib, elem
