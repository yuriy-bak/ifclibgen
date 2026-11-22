"""Update types in a project from library files.

Types are matched by GlobalId, LibGen_Parameters.ContentHash tells whether a
type changed. For a changed type the geometry, attributes, property sets and
type ports are replaced in place (the type entity and its GlobalId stay, so
occurrences keep their type). Occurrence ports are moved, not recreated, so
connections between elements are kept. If all ports of a type moved by the
same vector, the insertion point changed: by default occurrences are moved
back so that they stay where they were in the model.

No Blender here: works on ifcopenshell files, Bonsai calls it with tool.Ifc.get().
"""

import json
import os
from dataclasses import asdict, dataclass, field

import numpy as np

import ifcopenshell
import ifcopenshell.api.geometry
import ifcopenshell.api.project
import ifcopenshell.api.pset
import ifcopenshell.api.root
import ifcopenshell.api.system
import ifcopenshell.guid
import ifcopenshell.util.element
import ifcopenshell.util.system
import ifcopenshell.util.unit

HASH_PSET = "LibGen_Parameters"


# --------------------------------------------------------------------------
# placement math (own, file units: ifcopenshell's helpers changed between versions)


def _axis2(p):
    if p is None:
        return np.eye(4)
    m = np.eye(4)
    loc = list(p.Location.Coordinates) + [0.0] * (3 - len(p.Location.Coordinates))
    z = np.array(p.Axis.DirectionRatios if getattr(p, "Axis", None) else (0, 0, 1), float)
    x = np.array(p.RefDirection.DirectionRatios if getattr(p, "RefDirection", None) else (1, 0, 0), float)
    if len(z) == 2:
        z = np.array([0, 0, 1.0])
        x = np.array(list(x) + [0.0])
    z /= np.linalg.norm(z)
    x = x - z * x.dot(z)
    x /= np.linalg.norm(x)
    m[:3, 0], m[:3, 1], m[:3, 2], m[:3, 3] = x, np.cross(z, x), z, loc
    return m


def world_matrix(placement):
    if placement is None or not placement.is_a("IfcLocalPlacement"):
        return np.eye(4)
    return world_matrix(placement.PlacementRelTo) @ _axis2(placement.RelativePlacement)


def _set_world(f, product, m):
    ifcopenshell.api.geometry.edit_object_placement(f, product=product, matrix=m, is_si=False)


def _tol(f):
    return 0.001 / ifcopenshell.util.unit.calculate_unit_scale(f)  # 1 mm


# --------------------------------------------------------------------------
# plan


@dataclass
class PortChange:
    name: str
    kind: str          # same | moved | added | removed
    delta: list = field(default_factory=list)


@dataclass
class TypePlan:
    type_id: int
    guid: str
    name: str
    status: str        # unchanged | changed | missing (not in libraries)
    library: str = ""
    occurrences: int = 0
    ports: list = field(default_factory=list)       # [PortChange]
    shift: list = None                              # common port shift, project units, type axes
    keep_position: bool = True                      # with a shift: move occurrences back


@dataclass
class Row:
    """One line of the report: type, element, storey, what's wrong."""
    type_name: str
    element_guid: str
    element_name: str
    storey: str
    issue: str
    checked: bool = False


def type_hash(element):
    return (ifcopenshell.util.element.get_pset(element, HASH_PSET) or {}).get("ContentHash")


def library_types(paths):
    """{GlobalId: (path, ContentHash)} of all library types with a hash."""
    out = {}
    for path in paths:
        try:
            lib = ifcopenshell.open(path)
        except Exception:
            continue
        for t in lib.by_type("IfcTypeProduct"):
            h = type_hash(t)
            if h:
                out.setdefault(t.GlobalId, (path, h))
    return out


def library_files(library_dir):
    if not os.path.isdir(library_dir):
        return []
    return sorted(os.path.join(library_dir, n) for n in os.listdir(library_dir) if n.lower().endswith(".ifc"))


def _type_ports(t, scale=1.0):
    out = {}
    for p in ifcopenshell.util.system.get_ports(t):
        m = world_matrix(p.ObjectPlacement)
        m[:3, 3] *= scale
        out[p.Name or f"#{p.id()}"] = m
    return out


def _scale(lib, project):
    return ifcopenshell.util.unit.calculate_unit_scale(lib) / ifcopenshell.util.unit.calculate_unit_scale(project)


def plan_update(project, library_paths, type_ids=None):
    """Compare project types (all library-made ones, or type_ids) with the libraries."""
    index = library_types(library_paths)
    libs = {}
    tol = _tol(project)
    plans = []
    for t in project.by_type("IfcTypeProduct"):
        if type_ids is not None and t.id() not in type_ids:
            continue
        h = type_hash(t)
        if not h:
            continue
        occ = len(ifcopenshell.util.element.get_types(t))
        if t.GlobalId not in index:
            plans.append(TypePlan(t.id(), t.GlobalId, t.Name or "", "missing", occurrences=occ))
            continue
        path, lh = index[t.GlobalId]
        if lh == h:
            plans.append(TypePlan(t.id(), t.GlobalId, t.Name or "", "unchanged", path, occ))
            continue
        lib = libs.get(path) or libs.setdefault(path, ifcopenshell.open(path))
        lt = lib.by_guid(t.GlobalId)
        old, new = _type_ports(t), _type_ports(lt, _scale(lib, project))
        changes, deltas = [], []
        for name, m in old.items():
            if name not in new:
                changes.append(PortChange(name, "removed"))
                continue
            d = new[name][:3, 3] - m[:3, 3]
            same_axes = np.allclose(new[name][:3, :3], m[:3, :3], atol=1e-4)
            deltas.append(d if same_axes else None)
            moved = np.linalg.norm(d) > tol or not same_axes
            changes.append(PortChange(name, "moved" if moved else "same", [round(float(c), 3) for c in d]))
        changes += [PortChange(n, "added") for n in new if n not in old]
        shift = None
        if deltas and all(d is not None for d in deltas):
            d0 = deltas[0]
            if np.linalg.norm(d0) > tol and all(np.linalg.norm(d - d0) <= tol for d in deltas):
                shift = [round(float(c), 4) for c in d0]
        plans.append(TypePlan(t.id(), t.GlobalId, t.Name or "", "changed", path, occ, changes, shift))
    return plans


# --------------------------------------------------------------------------
# apply


def _storey(el):
    c = ifcopenshell.util.element.get_container(el)
    return (c.Name or c.LongName or "") if c else ""


def in_project_units(lib, project):
    """append_asset does not convert units: bring the library to the project's length unit."""
    if abs(_scale(lib, project) - 1) < 1e-9:
        return lib
    u = ifcopenshell.util.unit.get_project_unit(project, "LENGTHUNIT")
    name = (u.Prefix or "") + "METER" if u.is_a("IfcSIUnit") else u.Name.upper()
    return ifcopenshell.util.unit.convert_file_length_units(lib, name)


def _open_library(path, project):
    """A fresh copy of the library in project units with new GlobalIds everywhere, so
    append_asset never reuses the project's existing entities (ports, rels share stable ids)."""
    lib = in_project_units(ifcopenshell.open(path), project)
    new_guid = {}
    for e in lib.by_type("IfcRoot"):
        g = ifcopenshell.guid.new()
        new_guid[e.GlobalId] = g
        e.GlobalId = g
    return lib, new_guid


def _match_ports(occ_ports, inv, type_ports, tol):
    """Occurrence port -> type port name, by position/axis relative to the element
    (Bonsai's copies have no names). Ports already carrying a type port name win."""
    out, free = {}, dict(type_ports)
    for p in occ_ports:
        if p.Name in free:
            out[p] = p.Name
            del free[p.Name]
    for p in occ_ports:
        if p in out:
            continue
        local = inv @ world_matrix(p.ObjectPlacement)
        best = None
        for name, m in free.items():
            dist = np.linalg.norm(local[:3, 3] - m[:3, 3])
            if dist <= tol and local[:3, 2].dot(m[:3, 2]) > 0.999 and (best is None or dist < best[0]):
                best = (dist, name)
        if best:
            out[p] = best[1]
            del free[best[1]]
    return out


def _other(port):
    c = ifcopenshell.util.system.get_connected_port(port)
    el = ifcopenshell.util.system.get_port_element(c) if c else None
    return el.Name or el.is_a() if el else None


def _replace_type_data(project, t, nt):
    """Move geometry, attributes, psets and ports of the appended copy nt into t, remove nt."""
    nmaps = list(nt.RepresentationMaps or [])
    key = lambda m: (m.MappedRepresentation.ContextOfItems.id(), m.MappedRepresentation.RepresentationIdentifier)
    by_key = {key(m): m for m in nmaps}
    # The type's representation entities stay (Bonsai keeps their ids on meshes), only
    # their items are swapped with the new ones; the new shells then take the old items away.
    spent = []
    for om in t.RepresentationMaps or []:
        nm = by_key.pop(key(om), None)
        if nm is None:
            continue
        orep, nrep = om.MappedRepresentation, nm.MappedRepresentation
        orep.Items, nrep.Items = nrep.Items, orep.Items
        orep.RepresentationType, orep.RepresentationIdentifier = nrep.RepresentationType, nrep.RepresentationIdentifier
        spent.append((nm, nrep, om.MappingOrigin))
        om.MappingOrigin = nm.MappingOrigin
    extra = list(by_key.values())
    if extra:
        t.RepresentationMaps = list(t.RepresentationMaps or []) + extra
    nt.RepresentationMaps = None
    for nm, nrep, origin in spent:
        project.remove(nm)
        ifcopenshell.api.geometry.remove_representation(project, representation=nrep)
        if origin is not None and not project.get_total_inverses(origin):
            ifcopenshell.util.element.remove_deep2(project, origin)

    for attr in ("Name", "Description", "PredefinedType", "ElementType", "Tag"):
        if hasattr(t, attr) and hasattr(nt, attr):
            setattr(t, attr, getattr(nt, attr))

    for ps in list(t.HasPropertySets or []):
        ifcopenshell.api.pset.remove_pset(project, product=t, pset=ps)
    t.HasPropertySets, nt.HasPropertySets = nt.HasPropertySets, None

    for p in ifcopenshell.util.system.get_ports(t):
        ifcopenshell.api.root.remove_product(project, product=p)
    for rel in list(nt.IsNestedBy or []):
        rel.RelatingObject = t
    ifcopenshell.api.root.remove_product(project, product=nt)


def apply_update(project, plans):
    """Apply the changed plans. Returns report rows."""
    rows = []
    tol = _tol(project)
    libs = {}
    for plan in plans:
        if plan.status != "changed":
            continue
        t = project.by_id(plan.type_id)
        if plan.library not in libs:
            libs[plan.library] = _open_library(plan.library, project)
        lib, new_guid = libs[plan.library]
        lt = lib.by_guid(new_guid[plan.guid])

        old_ports = _type_ports(t)
        before = []
        for occ in ifcopenshell.util.element.get_types(t):
            m = world_matrix(occ.ObjectPlacement)
            ports = ifcopenshell.util.system.get_ports(occ)
            match = _match_ports(ports, np.linalg.inv(m), old_ports, tol)
            before.append((occ, m, {p: world_matrix(p.ObjectPlacement) for p in ports}, match))

        nt = ifcopenshell.api.project.append_asset(project, library=lib, element=lt)
        _replace_type_data(project, t, nt)
        new_ports = {p.Name: p for p in ifcopenshell.util.system.get_ports(t)}
        new_local = {n: world_matrix(p.ObjectPlacement) for n, p in new_ports.items()}

        back = np.eye(4)
        if plan.shift and plan.keep_position:
            back[:3, 3] = -np.array(plan.shift, float)
        for occ, m, pw, match in before:
            row = lambda issue: rows.append(Row(t.Name or "", occ.GlobalId, occ.Name or "", _storey(occ), issue))
            m2 = m @ back
            if not np.allclose(m2, m):
                _set_world(project, occ, m2)
            for p, name in match.items():
                other = _other(p)
                if name not in new_local:
                    if other:
                        ifcopenshell.api.system.disconnect_port(project, port=p)
                        row(f"Port {name!r} removed from the type, disconnected from {other}")
                    ifcopenshell.api.root.remove_product(project, product=p)
                    continue
                w = m2 @ new_local[name]
                _set_world(project, p, w)
                tp = new_ports[name]
                p.Name, p.FlowDirection = name, tp.FlowDirection
                p.PredefinedType, p.SystemType = tp.PredefinedType, tp.SystemType
                moved = np.linalg.norm(w[:3, 3] - pw[p][:3, 3])
                if other and (moved > tol or w[:3, 2].dot(pw[p][:3, 2]) < 0.999):
                    row(f"Port {name!r} moved by {moved * ifcopenshell.util.unit.calculate_unit_scale(project) * 1000:.0f} mm, "
                        f"check the connection to {other}")
            for name in new_local:
                if name in match.values():
                    continue
                tp = new_ports[name]
                p = ifcopenshell.api.system.add_port(project, element=occ)
                p.Name, p.FlowDirection = name, tp.FlowDirection
                p.PredefinedType, p.SystemType = tp.PredefinedType, tp.SystemType
                _set_world(project, p, m2 @ new_local[name])
            unmatched = [p for p in pw if p not in match]
            if unmatched:
                row(f"{len(unmatched)} port(s) of the element do not match the type, left as they were")
    return rows


# --------------------------------------------------------------------------
# report file


def report_path(project_path):
    return os.path.splitext(project_path)[0] + ".libupdate.json"


def save_report(path, rows, plans=()):
    data = {"types": [asdict(p) for p in plans], "rows": [asdict(r) for r in rows]}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)


def load_report(path):
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    return [Row(**r) for r in data.get("rows", [])]
