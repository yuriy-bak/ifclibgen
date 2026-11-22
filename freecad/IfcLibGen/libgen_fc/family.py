"""A FreeCAD document as a family of library types.

The document holds:
- a LibFamily object (IFC class, name template, manufacturer, group...);
- a "Params" spreadsheet: cells with aliases (Width, Height, ...) that drive the model by
  expressions; FreeCAD refuses unit names as aliases (W, H, L, F, A...): use words;
- a "Sizes" spreadsheet: row 1 = aliases from Params (optionally a "Name" column),
  one row per size;
- the bodies (visible solids) and ports: Part LCS objects attached to a face, with
  port properties. The document origin is the insertion point of the type.

Export runs every size: sets the Params cells, recomputes, tessellates the bodies,
reads the ports from their faces and writes a package plus the group .ifc file with
the ifclibgen core. No GUI needed: works in freecadcmd too.
"""

import math
import os
import re

import FreeCAD as App
import Part

IFC_CLASSES = [
    "IfcAirTerminalType", "IfcDamperType", "IfcDuctSilencerType", "IfcAirTerminalBoxType", "IfcFanType",
    "IfcFilterType", "IfcAirToAirHeatRecoveryType", "IfcCoilType", "IfcUnitaryEquipmentType", "IfcFlowMeterType",
    "IfcDuctFittingType", "IfcDuctSegmentType", "IfcBuildingElementProxyType",
]
FLOWS = ["SOURCEANDSINK", "SINK", "SOURCE"]
SYSTEMS = ["supply", "return", "exhaust", "air"]
PREF = "User parameter:BaseApp/Preferences/Mod/IfcLibGen"


def library_dir():
    return App.ParamGet(PREF).GetString("LibraryDir", "")


def set_library_dir(path):
    App.ParamGet(PREF).SetString("LibraryDir", path)


def _slug(s):
    return re.sub(r"[^\w.-]+", "-", s.strip().lower(), flags=re.UNICODE).strip("-") or "family"


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return v
    return int(f) if f == int(f) else f


# --------------------------------------------------------------------------
# family object


class Family:
    """Proxy of the LibFamily object."""

    def __init__(self, obj):
        obj.Proxy = self
        self.setup(obj)

    def setup(self, obj):
        def add(kind, name, group, doc, value=None):
            if not hasattr(obj, name):
                obj.addProperty(kind, name, group, doc)
                if value is not None:
                    setattr(obj, name, value)
        add("App::PropertyString", "TypeName", "Type", "Name of each type; {Alias} is replaced by the size value",
            "Example {Width}x{Height}")
        add("App::PropertyEnumeration", "IfcClass", "Type", "IFC type class")
        if not obj.getEnumerationsOfProperty("IfcClass"):
            obj.IfcClass = IFC_CLASSES
        add("App::PropertyString", "PredefinedType", "Type", "E.g. CONTROLDAMPER, DIFFUSER; USERDEFINED to use ElementType",
            "NOTDEFINED")
        add("App::PropertyString", "ElementType", "Type", "Used with USERDEFINED")
        add("App::PropertyString", "Description", "Type", "Description of the types")
        add("App::PropertyString", "Insertion", "Type", "Where the insertion point (document origin) is")
        add("App::PropertyString", "Manufacturer", "Manufacturer", "Manufacturer")
        add("App::PropertyString", "Model", "Manufacturer", "Model / series")
        add("App::PropertyString", "Group", "Library", "Library file the types go to: <library folder>/<group>.ifc",
            "custom")
        add("App::PropertyLink", "Params", "Sizes", "Spreadsheet with aliases that drive the model")
        add("App::PropertyLink", "Sizes", "Sizes", "Spreadsheet: row 1 = aliases of Params, one row per size")
        add("App::PropertyLinkList", "Bodies", "Geometry", "Solids that make the type; empty = all visible solids")
        add("App::PropertyFloat", "Deflection", "Geometry", "Mesh accuracy, mm", 0.5)

    def onDocumentRestored(self, obj):
        self.setup(obj)

    def execute(self, obj):
        pass

    def dumps(self):
        return None

    def loads(self, state):
        return None


def find_family(doc):
    return next((o for o in doc.Objects if getattr(o, "Proxy", None).__class__.__name__ == "Family"), None)


def create_family(doc):
    fam = find_family(doc)
    if fam:
        return fam
    fam = doc.addObject("App::FeaturePython", "LibFamily")
    Family(fam)
    params = doc.getObject("Params") or doc.addObject("Spreadsheet::Sheet", "Params")
    sizes = doc.getObject("Sizes") or doc.addObject("Spreadsheet::Sheet", "Sizes")
    if not params.getUsedCells():
        for row, (alias, val, note) in enumerate([("Width", "400", "mm"), ("Height", "300", "mm")], start=1):
            params.set(f"A{row}", alias)
            params.set(f"B{row}", val)
            params.setAlias(f"B{row}", alias)
            params.set(f"C{row}", note)
    if not sizes.getUsedCells():
        for c, v in zip("AB", ("Width", "Height")):
            sizes.set(f"{c}1", v)
        for r, (w, h) in enumerate([(200, 200), (400, 300), (600, 400)], start=2):
            sizes.set(f"A{r}", str(w))
            sizes.set(f"B{r}", str(h))
    fam.Params, fam.Sizes = params, sizes
    doc.recompute()
    if App.GuiUp:
        try:
            fam.ViewObject.Proxy = 0
        except Exception:
            pass
    return fam


# --------------------------------------------------------------------------
# ports


def is_port(o):
    return hasattr(o, "IsLibPort") and o.IsLibPort


def ports(doc):
    return [o for o in doc.Objects if is_port(o)]


def make_port(doc, obj, sub, name=None):
    """A Part LCS attached to the face (origin at its centre, follows the model)."""
    lcs = doc.addObject("Part::LocalCoordinateSystem", "Port")
    for kind, prop, doc_ in [
        ("App::PropertyBool", "IsLibPort", "Marks a library port"),
        ("App::PropertyString", "PortName", "Port name, e.g. Inlet"),
        ("App::PropertyEnumeration", "PortShape", "Auto: from the face (circle or rectangle)"),
        ("App::PropertyLength", "Diameter", "0 = from the face"),
        ("App::PropertyLength", "Width", "Along the longest straight edge of the face; 0 = from the face"),
        ("App::PropertyLength", "Height", "0 = from the face"),
        ("App::PropertyEnumeration", "Flow", "SINK: air flows in, SOURCE: out"),
        ("App::PropertyEnumeration", "System", "Air system"),
    ]:
        lcs.addProperty(kind, prop, "Port", doc_)
    lcs.IsLibPort = True
    lcs.setEditorMode("IsLibPort", 2)
    lcs.PortShape = ["Auto", "Round", "Rectangular"]
    lcs.Flow = FLOWS
    lcs.System = SYSTEMS
    n = len(ports(doc))
    lcs.PortName = name or f"Port{n}"
    lcs.Label = f"Port {lcs.PortName}"
    lcs.AttachmentSupport = [(obj, sub)]
    lcs.MapMode = "InertialCS"
    doc.recompute()
    # turn the marker so its Z points out of the face (visual only: export reads the face)
    try:
        f = port_face(lcs)
        fr = face_frame(f)
        want = App.Rotation(App.Vector(*fr["xdir"]), App.Vector(), App.Vector(*fr["dir"]), "ZXY")
        lcs.AttachmentOffset = App.Placement(App.Vector(), lcs.Placement.Rotation.inverted().multiply(want))
        doc.recompute()
    except Exception:
        pass
    return lcs


def port_face(lcs):
    sup = lcs.AttachmentSupport
    if not sup:
        raise ValueError(f"{lcs.Label}: not attached to a face")
    obj, subs = sup[0]
    sub = subs[0] if isinstance(subs, (list, tuple)) else subs
    if not sub.startswith("Face"):
        raise ValueError(f"{lcs.Label}: attached to {sub}, not to a face")
    try:
        f = Part.getShape(obj, sub, needSubElement=True)
    except Exception:
        f = obj.Shape.getElement(sub)
    if f.isNull() or f.ShapeType != "Face":
        raise ValueError(f"{lcs.Label}: face {sub} of {obj.Label} not found")
    return f


def _circle(wire):
    edges = wire.Edges
    if edges and all(isinstance(e.Curve, Part.Circle) for e in edges):
        r = edges[0].Curve.Radius
        c = edges[0].Curve.Center
        if all(abs(e.Curve.Radius - r) < 1e-6 and (e.Curve.Center - c).Length < 1e-6 for e in edges):
            return r, c
    return None


def face_frame(face):
    """Port frame and size from a planar face. A face with holes (flange ring): the
    largest hole is the opening; otherwise the outer outline."""
    if not isinstance(face.Surface, Part.Plane):
        raise ValueError("port face is not flat")
    u0, u1, v0, v1 = face.ParameterRange
    z = face.normalAt((u0 + u1) / 2, (v0 + v1) / 2)
    inner = [w for w in face.Wires if not w.isSame(face.OuterWire)]
    wire = max(inner, key=lambda w: Part.Face(w).Area) if inner else face.OuterWire
    circ = _circle(wire)
    if circ:
        r, centre = circ
        x = App.Vector(0, 0, 1).cross(z)
        if x.Length < 1e-6:
            x = App.Vector(1, 0, 0)
        x.normalize()
        return {"at": centre, "dir": z, "xdir": x, "shape": "round", "d": 2 * r}
    centre = Part.Face(wire).CenterOfMass if inner else face.CenterOfMass
    lines = [e for e in wire.Edges if isinstance(e.Curve, Part.Line)]
    if lines:
        e = max(lines, key=lambda e: e.Length)
        x = e.Vertexes[-1].Point - e.Vertexes[0].Point
        x = x - z * x.dot(z)
    else:
        x = App.Vector(0, 0, 1).cross(z)
        if x.Length < 1e-6:
            x = App.Vector(1, 0, 0)
    x.normalize()
    # keep a stable sense: X towards +world axes where possible
    if x.dot(App.Vector(1, 1, 1)) < 0:
        x = -x
    y = z.cross(x)
    pts = [v.Point for v in wire.Vertexes] + [e.discretize(8)[i] for e in wire.Edges for i in range(8)]
    xs = [(p - centre).dot(x) for p in pts]
    ys = [(p - centre).dot(y) for p in pts]
    return {"at": centre, "dir": z, "xdir": x, "shape": "rect", "w": max(xs) - min(xs), "h": max(ys) - min(ys)}


def port_def(lcs):
    """Port dict for the core (type coordinates = document coordinates, mm)."""
    fr = face_frame(port_face(lcs))
    shape = {"Round": "round", "Rectangular": "rect"}.get(lcs.PortShape, fr["shape"])
    d = {"name": lcs.PortName or lcs.Label,
         "at": [round(c, 3) for c in fr["at"]],
         "dir": [round(c, 6) for c in fr["dir"]],
         "xdir": [round(c, 6) for c in fr["xdir"]],
         "shape": shape, "flow": lcs.Flow, "system": lcs.System}
    if shape == "round":
        d["d"] = round(float(lcs.Diameter) or fr.get("d") or max(fr.get("w", 0), fr.get("h", 0)), 2)
    else:
        d["w"] = round(float(lcs.Width) or fr.get("w") or fr.get("d", 0), 2)
        d["h"] = round(float(lcs.Height) or fr.get("h") or fr.get("d", 0), 2)
    return d


# --------------------------------------------------------------------------
# sizes, bodies


def _alias_cell(sheet, alias):
    try:
        return sheet.getCellFromAlias(alias)
    except Exception:
        return None


def read_sizes(fam):
    """[{alias: value}] from the Sizes sheet (row 1 = aliases); 'Name' column kept as text."""
    sheet = fam.Sizes
    if sheet is None:
        return []
    cells = sheet.getUsedCells()
    cols = sorted({re.match(r"([A-Z]+)", c).group(1) for c in cells}, key=lambda c: (len(c), c))
    rows = sorted({int(re.match(r"[A-Z]+(\d+)", c).group(1)) for c in cells})
    head = {c: str(sheet.getContents(f"{c}1")).strip().lstrip("'") for c in cols}
    out = []
    for r in rows:
        if r == 1:
            continue
        row = {}
        for c, h in head.items():
            if not h:
                continue
            raw = str(sheet.getContents(f"{c}{r}")).strip().lstrip("'=")
            if raw == "":
                continue
            row[h] = raw if h.lower() == "name" else _num(raw)
        if row:
            out.append(row)
    return out


def _consumed(o):
    """Used as input of another shape (Cut tool, array base, body feature...)."""
    for u in o.InList:
        if is_port(u) or u.TypeId.startswith(("Spreadsheet", "App::FeaturePython", "Part::LocalCoordinateSystem")):
            continue
        if hasattr(u, "Shape"):
            return True
    return False


def bodies(doc, fam):
    """Solids of the type: LibFamily.Bodies, or every final solid (not used by another
    feature); if some of them are visible, only the visible ones."""
    if fam.Bodies:
        return [o for o in fam.Bodies if hasattr(o, "Shape")]
    out = []
    for o in doc.Objects:
        if is_port(o) or not hasattr(o, "Shape"):
            continue
        if o.TypeId.startswith(("App::", "Spreadsheet", "Part::LocalCoordinateSystem", "PartDesign::CoordinateSystem")):
            continue
        if o.TypeId.startswith("PartDesign::") and o.TypeId != "PartDesign::Body":
            continue  # features inside a body: the body itself is taken
        try:
            if o.Shape.isNull() or not o.Shape.Solids or _consumed(o):
                continue
        except Exception:
            continue
        out.append(o)
    visible = [o for o in out if getattr(o, "Visibility", True)]
    return visible or out


def _style(o):
    try:
        app = o.ViewObject.ShapeAppearance[0]
        rgb = [round(c, 3) for c in app.DiffuseColor[:3]]
        return rgb, round(float(app.Transparency), 3)
    except Exception:
        return [0.75, 0.75, 0.75], 0.0


def _mesh(o, deflection):
    from ifclibgen.package import Part as MeshPart
    shape = o.Shape if o.TypeId == "PartDesign::Body" else Part.getShape(o)
    pts, tris = shape.tessellate(deflection)
    coords = [[round(p.x, 4), round(p.y, 4), round(p.z, 4)] for p in pts]
    return MeshPart(coords, [(list(t), []) for t in tris], closed=shape.isClosed() if shape.Solids else False)


# --------------------------------------------------------------------------
# run sizes


class SizeRunner:
    """Applies sizes to the Params sheet and restores the original values afterwards."""

    def __init__(self, doc, fam):
        self.doc, self.sheet = doc, fam.Params
        self.saved = {}

    def apply(self, row):
        for alias, val in row.items():
            if alias.lower() == "name":
                continue
            cell = _alias_cell(self.sheet, alias)
            if cell is None:
                raise ValueError(f"Sizes column {alias!r}: no such alias in {self.sheet.Label}")
            self.saved.setdefault(cell, self.sheet.getContents(cell))
            self.sheet.set(cell, str(val))
        self.doc.recompute()
        bad = [o.Label for o in self.doc.Objects if "Invalid" in o.State or "Error" in o.State]
        if bad:
            raise ValueError("recompute failed: " + ", ".join(bad))

    def restore(self):
        for cell, v in self.saved.items():
            self.sheet.set(cell, v)
        self.doc.recompute()


def type_name(fam, row):
    if row.get("Name"):
        return str(row["Name"])
    vals = {k: _num(v) for k, v in row.items()}
    try:
        return fam.TypeName.format(**vals)
    except (KeyError, IndexError, ValueError) as e:
        raise ValueError(f"TypeName {fam.TypeName!r}: unknown {e}")


def build_package(doc, fam, progress=None):
    from ifclibgen.package import Package, TypeDef
    sizes = read_sizes(fam)
    if not sizes:
        raise ValueError("Sizes sheet has no rows")
    group = _slug(fam.Group or "custom")
    ifc = {"class": fam.IfcClass, "predefined": fam.PredefinedType.strip() or "NOTDEFINED"}
    if fam.ElementType.strip():
        ifc["element_type"] = fam.ElementType.strip()
    styles, types = {}, []
    runner = SizeRunner(doc, fam)
    try:
        for i, row in enumerate(sizes):
            name = type_name(fam, row)
            if progress:
                progress(i, len(sizes), name)
            runner.apply(row)
            parts = []
            for o in bodies(doc, fam):
                p = _mesh(o, fam.Deflection or 0.5)
                rgb, tr = _style(o)
                key = "rgb-" + "".join(f"{int(c * 255):02x}" for c in rgb) + (f"-t{int(tr * 100)}" if tr else "")
                styles[key] = {"rgb": rgb, "transparency": tr}
                p.style = key
                parts.append(p)
            if not parts:
                raise ValueError("no visible solids to export")
            psets = {}
            mi = {k: v for k, v in (("Manufacturer", fam.Manufacturer), ("ModelLabel", fam.Model)) if v}
            if mi:
                mi["ModelReference"] = name
                psets["Pset_ManufacturerTypeInformation"] = mi
            params = {k: v for k, v in row.items() if k.lower() != "name"}
            types.append(TypeDef(name, parts, description=fam.Description, insertion=fam.Insertion,
                                 params=params, ports=[port_def(p) for p in ports(doc)], psets=psets))
    finally:
        runner.restore()
    src = f"freecad/{group}/{_slug(fam.Model or doc.Name)}"
    return Package(src, ifc, fam.Manufacturer, group, styles, types)


def export(doc, fam, lib_dir, progress=None):
    """Package into <lib>/packages/<group>/<family>/, rebuild <lib>/<group>.ifc."""
    from ifclibgen import core
    from ifclibgen.package import save_package
    pkg = build_package(doc, fam, progress)
    folder = os.path.join(lib_dir, "packages", pkg.group, pkg.source.split("/")[-1])
    save_package(pkg, folder)
    path, n = core.rebuild_group(lib_dir, pkg.group)
    return pkg, path, n


# --------------------------------------------------------------------------
# check


def check(doc, fam=None):
    """[(level, message)], level: error | warning | ok."""
    out = []
    fam = fam or find_family(doc)
    if fam is None:
        return [("error", "No LibFamily object: use 'New family'")]
    if fam.Params is None or fam.Sizes is None:
        out.append(("error", "LibFamily: Params or Sizes spreadsheet not set"))
        return out
    sizes = read_sizes(fam)
    if not sizes:
        out.append(("error", "Sizes: no rows"))
    heads = sorted({k for r in sizes for k in r if k.lower() != "name"})
    for h in heads:
        if _alias_cell(fam.Params, h) is None:
            out.append(("error", f"Sizes column {h!r}: no such alias in Params"))
    pl = ports(doc)
    if not pl:
        out.append(("warning", "No ports: use 'Port on face'"))
    names = [p.PortName for p in pl]
    for n in {n for n in names if names.count(n) > 1}:
        out.append(("error", f"Port name {n!r} used twice"))
    if not bodies(doc, fam):
        out.append(("error", "No visible solids"))
    if any(e[0] == "error" for e in out):
        return out
    runner = SizeRunner(doc, fam)
    seen = set()
    try:
        for row in sizes:
            try:
                name = type_name(fam, row)
                if name in seen:
                    out.append(("error", f"Type name {name!r} repeats: add the varying size to TypeName"))
                seen.add(name)
                runner.apply(row)
                for o in bodies(doc, fam):
                    s = o.Shape
                    if not s.isValid():
                        out.append(("warning", f"{name}: {o.Label} has an invalid shape"))
                    elif not s.isClosed():
                        out.append(("warning", f"{name}: {o.Label} is not closed"))
                for p in pl:
                    d = port_def(p)
                    if d["shape"] == "round" and not d["d"] or d["shape"] == "rect" and not (d["w"] and d["h"]):
                        out.append(("error", f"{name}: port {p.PortName} has no size"))
            except Exception as e:
                out.append(("error", f"{row}: {e}"))
    finally:
        runner.restore()
    if not any(e[0] == "error" for e in out):
        out.append(("ok", f"{len(sizes)} size(s), {len(pl)} port(s): ready to export"))
    return out
