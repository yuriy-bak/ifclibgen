"""Builds example-damper.FCStd: a made-up rectangular damper family.

    freecadcmd make_example_damper.py [out.FCStd]

Shows the conventions: Params sheet with aliases, Sizes sheet, solids driven by
expressions, ports attached to the flange faces, document origin = centre of the inlet.
"""
import os
import sys

import FreeCAD as App

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, "..", "IfcLibGen"), os.path.join(HERE, "..", "..")]
from libgen_fc import family  # noqa: E402

out = os.environ.get("FCSTD_OUT") or (sys.argv[-1] if sys.argv[-1].endswith(".FCStd") else None) or os.path.join(HERE, "example-damper.FCStd")
doc = App.newDocument("ExampleDamper")
fam = family.create_family(doc)
P = fam.Params
for r, (alias, val, note) in enumerate([("Width", "400", "mm"), ("Height", "300", "mm"),
                                        ("Length", "150", "mm"), ("Flange", "20", "mm")], start=1):
    P.set(f"A{r}", alias)
    P.set(f"B{r}", val)
    P.setAlias(f"B{r}", alias)
    P.set(f"C{r}", note)
S = fam.Sizes
S.clearAll()
S.set("A1", "Width")
S.set("B1", "Height")
r = 2
for w in (200, 300, 400, 500, 600):
    for h in (200, 300, 400):
        if h <= w:
            S.set(f"A{r}", str(w))
            S.set(f"B{r}", str(h))
            r += 1
fam.TypeName = "Example damper {Width}x{Height}"
fam.IfcClass = "IfcDamperType"
fam.PredefinedType = "CONTROLDAMPER"
fam.Manufacturer = "Example"
fam.Model = "ED"
fam.Group = "example-dampers"
fam.Description = "Rectangular control damper (made up)"
fam.Insertion = "centre of the inlet flange, axis along +X"
doc.recompute()


def box(name, length, width, height, x, y, z):
    b = doc.addObject("Part::Box", name)
    for prop, e in (("Length", length), ("Width", width), ("Height", height),
                    (".Placement.Base.x", x), (".Placement.Base.y", y), (".Placement.Base.z", z)):
        b.setExpression(prop, e)
    return b


def ring(name, length, outer_w, outer_h, x, wall):
    """Rectangular tube along X: outer box minus inner box (W x H opening)."""
    o = box(name + "Outer", length, outer_w, outer_h, x, f"-({outer_w}) / 2", f"-({outer_h}) / 2")
    i = box(name + "Inner", f"{length} + 2", "Params.Width", "Params.Height", f"{x} - 1", "-Params.Width / 2", "-Params.Height / 2")
    c = doc.addObject("Part::Cut", name)
    c.Base, c.Tool = o, i
    o.Visibility = i.Visibility = False
    return c


casing = ring("Casing", "Params.Length", "Params.Width + 3", "Params.Height + 3", "0", 1.5)
inlet = ring("InletFlange", "4", "Params.Width + 2 * Params.Flange", "Params.Height + 2 * Params.Flange", "0", 0)
outlet = ring("OutletFlange", "4", "Params.Width + 2 * Params.Flange", "Params.Height + 2 * Params.Flange", "Params.Length - 4", 0)
blade = box("Blade", "100", "Params.Width - 4", "2", "Params.Length / 2 - 50", "-(Params.Width - 4) / 2", "-1")
doc.recompute()


def face(obj, x, nx):
    for i, f in enumerate(obj.Shape.Faces, start=1):
        u0, u1, v0, v1 = f.ParameterRange
        n = f.normalAt((u0 + u1) / 2, (v0 + v1) / 2)
        if abs(f.CenterOfMass.x - x) < 1e-6 and abs(n.x - nx) < 1e-6:
            return f"Face{i}"
    raise RuntimeError(f"no face at x={x} in {obj.Label}")


pin = family.make_port(doc, inlet, face(inlet, 0, -1), "Inlet")
pin.Flow = "SINK"
pout = family.make_port(doc, outlet, face(outlet, float(fam.Params.Length), 1), "Outlet")
pout.Flow = "SOURCE"
doc.recompute()
COLOURS = [("Casing", (0.70, 0.72, 0.75)), ("InletFlange", (0.70, 0.72, 0.75)), ("OutletFlange", (0.70, 0.72, 0.75)), ("Blade", (0.15, 0.15, 0.15))]

if App.GuiUp:  # colours and visibility are stored only when built in the GUI
    import FreeCADGui as Gui
    for o in doc.Objects:
        if hasattr(o, "ViewObject") and o.ViewObject and hasattr(o, "Shape") and not family.is_port(o):
            o.ViewObject.Visibility = False
    for name, rgb in COLOURS:
        o = doc.getObject(name)
        if o is not None:
            o.ViewObject.Visibility = True
            o.ViewObject.ShapeColor = rgb
    for p in family.ports(doc):
        p.ViewObject.Visibility = True
doc.saveAs(out)
print("saved", out)
