"""Update types in a project from the library (ifclibgen.update)."""

import numpy as np

import ifcopenshell
import ifcopenshell.api.aggregate
import ifcopenshell.api.context
import ifcopenshell.api.project
import ifcopenshell.api.root
import ifcopenshell.api.spatial
import ifcopenshell.api.system
import ifcopenshell.api.type
import ifcopenshell.api.unit
import ifcopenshell.util.element
import ifcopenshell.util.system

from ifclibgen import core, update
from ifclibgen.package import Package, Part, TypeDef


def box(x0, x1):
    return Part([[x0, -50, -50], [x1, -50, -50], [x1, 50, -50], [x0, 50, -50],
                 [x0, -50, 50], [x1, -50, 50], [x1, 50, 50], [x0, 50, 50]],
                [([0, 3, 2, 1], []), ([4, 5, 6, 7], []), ([0, 1, 5, 4], []),
                 ([1, 2, 6, 5], []), ([2, 3, 7, 6], []), ([3, 0, 4, 7], [])])


def port(name, x, d):
    return {"name": name, "at": [x, 0, 0], "dir": [d, 0, 0], "shape": "rect", "w": 100, "h": 100}


def library(path, x0=0, x1=100, ports=None):
    ports = ports if ports is not None else [port("Inlet", x0, -1), port("Outlet", x1, 1)]
    pkg = Package("test/box", {"class": "IfcDuctSilencerType", "predefined": "RECTANGULAR"}, "T", "g", {},
                  [TypeDef("Box", [box(x0, x1)], ports=ports)])
    core.write_packages([pkg], str(path))
    return str(path)


def project_with(lib_path):
    """Project in metres, two occurrences in a row, a1.Outlet connected to a2.Inlet (like Bonsai does it)."""
    f = ifcopenshell.api.project.create_file()
    proj = ifcopenshell.api.root.create_entity(f, ifc_class="IfcProject", name="P")
    ifcopenshell.api.unit.assign_unit(f, units=[ifcopenshell.api.unit.add_si_unit(f, unit_type="LENGTHUNIT")])  # metres
    model = ifcopenshell.api.context.add_context(f, context_type="Model")
    ifcopenshell.api.context.add_context(f, context_type="Model", context_identifier="Body",
                                         target_view="MODEL_VIEW", parent=model)
    site = ifcopenshell.api.root.create_entity(f, ifc_class="IfcSite")
    storey = ifcopenshell.api.root.create_entity(f, ifc_class="IfcBuildingStorey", name="Этаж 2")
    ifcopenshell.api.aggregate.assign_object(f, products=[site], relating_object=proj)
    ifcopenshell.api.aggregate.assign_object(f, products=[storey], relating_object=site)
    lib = update.in_project_units(ifcopenshell.open(lib_path), f)
    t = ifcopenshell.api.project.append_asset(f, library=lib, element=lib.by_type("IfcDuctSilencerType")[0])
    occs = []
    for i, x in enumerate([1.0, 1.1]):
        o = ifcopenshell.api.root.create_entity(f, ifc_class="IfcDuctSilencer", name=f"a{i + 1}")
        ifcopenshell.api.spatial.assign_container(f, products=[o], relating_structure=storey)
        m = np.eye(4)
        m[0, 3] = x
        update._set_world(f, o, m)
        ifcopenshell.api.type.assign_type(f, related_objects=[o], relating_type=t)
        for tp in ifcopenshell.util.system.get_ports(t):
            p = ifcopenshell.api.system.add_port(f, element=o)
            update._set_world(f, p, m @ update.world_matrix(tp.ObjectPlacement))
        occs.append(o)
    a1_out = [p for p in ifcopenshell.util.system.get_ports(occs[0]) if update.world_matrix(p.ObjectPlacement)[0, 3] > 1.05][0]
    a2_in = [p for p in ifcopenshell.util.system.get_ports(occs[1]) if update.world_matrix(p.ObjectPlacement)[0, 3] < 1.15][0]
    ifcopenshell.api.system.connect_port(f, port1=a1_out, port2=a2_in)
    return f, t, occs


def xs(el):
    return sorted(round(float(update.world_matrix(p.ObjectPlacement)[0, 3]), 4) for p in ifcopenshell.util.system.get_ports(el))


def test_unchanged(tmp_path):
    lib = library(tmp_path / "g.ifc")
    f, t, _ = project_with(lib)
    plans = update.plan_update(f, [lib])
    assert [p.status for p in plans] == ["unchanged"]


def test_insertion_shift_keeps_position(tmp_path):
    f, t, (a1, a2) = project_with(library(tmp_path / "v1.ifc"))
    lib2 = library(tmp_path / "v2.ifc", -50, 50)  # same box, origin moved to the centre
    (plan,) = update.plan_update(f, [lib2])
    assert plan.status == "changed" and plan.occurrences == 2
    assert np.allclose(plan.shift, [-0.05, 0, 0])
    rows = update.apply_update(f, [plan])
    assert rows == []
    assert xs(a1) == [1.0, 1.1] and xs(a2) == [1.1, 1.2]
    assert round(update.world_matrix(a1.ObjectPlacement)[0, 3], 4) == 1.05
    out = [p for p in ifcopenshell.util.system.get_ports(a1) if p.Name == "Outlet"][0]
    assert ifcopenshell.util.system.get_port_element(ifcopenshell.util.system.get_connected_port(out)) == a2
    assert update.type_hash(t) == update.type_hash(ifcopenshell.open(lib2).by_type("IfcDuctSilencerType")[0])
    assert len(f.by_type("IfcDuctSilencerType")) == 1
    assert {p.Name for p in ifcopenshell.util.system.get_ports(a1)} == {"Inlet", "Outlet"}
    # occurrences still use the type geometry: one shape representation for the type
    assert len([r for r in f.by_type("IfcShapeRepresentation") if r.RepresentationType == "Tessellation"]) == 1


def test_accept_new_insertion_reports_connection(tmp_path):
    f, t, (a1, a2) = project_with(library(tmp_path / "v1.ifc"))
    (plan,) = update.plan_update(f, [library(tmp_path / "v2.ifc", -50, 50)])
    plan.keep_position = False
    rows = update.apply_update(f, [plan])
    assert round(update.world_matrix(a1.ObjectPlacement)[0, 3], 4) == 1.0
    assert xs(a1) == [0.95, 1.05]
    assert len(rows) == 2  # both ends of the connection moved
    assert rows[0].storey == "Этаж 2" and "50 mm" in rows[0].issue


def test_port_moved_and_removed(tmp_path):
    f, t, (a1, a2) = project_with(library(tmp_path / "v1.ifc"))
    (plan,) = update.plan_update(f, [library(tmp_path / "v2.ifc", 0, 150)])
    assert plan.shift is None
    assert {c.name: c.kind for c in plan.ports} == {"Inlet": "same", "Outlet": "moved"}
    rows = update.apply_update(f, [plan])
    assert xs(a1) == [1.0, 1.15]
    assert [(r.element_name, r.issue.split(",")[0]) for r in rows] == [("a1", "Port 'Outlet' moved by 50 mm")]

    (plan,) = update.plan_update(f, [library(tmp_path / "v3.ifc", 0, 150, [port("Inlet", 0, -1)])])
    rows = update.apply_update(f, [plan])
    assert len(ifcopenshell.util.system.get_ports(a1)) == 1
    assert len(rows) == 1 and "removed" in rows[0].issue
    assert ifcopenshell.util.system.get_connected_port(ifcopenshell.util.system.get_ports(a2)[0]) is None


def test_missing_and_report(tmp_path):
    f, t, _ = project_with(library(tmp_path / "v1.ifc"))
    (plan,) = update.plan_update(f, [])
    assert plan.status == "missing"
    rows = [update.Row("Box", "g", "a1", "Этаж 2", "x", checked=True)]
    path = tmp_path / "p.libupdate.json"
    update.save_report(str(path), rows, [plan])
    assert update.load_report(str(path)) == rows
