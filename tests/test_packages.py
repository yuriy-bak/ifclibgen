"""python -m pytest tests/"""

import os

import ifcopenshell

from ifclibgen import core
from ifclibgen.package import Package, Part, TypeDef, content_hash, load_package, save_package

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TPL = os.path.join(ROOT, "templates")
DATA = os.path.join(ROOT, "data")


def test_template_roundtrip(tmp_path):
    """Templates -> package on disk -> load: same content hash, same IFC types."""
    pkgs = core.template_packages(TPL, DATA, only=["example/damper"])
    assert pkgs
    pkg = pkgs[0][0]
    save_package(pkg, tmp_path / "damper")
    back = load_package(str(tmp_path / "damper"))
    assert [t.name for t in back.types] == [t.name for t in pkg.types]
    for a, b in zip(pkg.types, back.types):
        assert content_hash(pkg, a) == content_hash(back, b)
    out = tmp_path / "damper.ifc"
    core.write_packages([back], str(out))
    f = ifcopenshell.open(str(out))
    types = f.by_type("IfcDamperType")
    assert len(types) == len(pkg.types)
    t = types[0]
    assert t.GlobalId == core.stable_guid("example/damper", t.Name)


def test_rebuild_group(tmp_path):
    box = Part([[0, -50, -50], [100, -50, -50], [100, 50, -50], [0, 50, -50],
                [0, -50, 50], [100, -50, 50], [100, 50, 50], [0, 50, 50]],
               [([0, 3, 2, 1], []), ([4, 5, 6, 7], []), ([0, 1, 5, 4], []),
                ([1, 2, 6, 5], []), ([2, 3, 7, 6], []), ([3, 0, 4, 7], [])])
    for i, n in enumerate(["A", "B"]):
        pkg = Package(f"test/{n}", {"class": "IfcDuctSilencerType", "predefined": "RECTANGULAR"}, "T", "g",
                      {}, [TypeDef(f"Box {n}", [box], ports=[
                          {"name": "Inlet", "at": [0, 0, 0], "dir": [-1, 0, 0], "shape": "rect", "w": 100, "h": 100}])])
        save_package(pkg, tmp_path / "packages" / n)
    path, n = core.rebuild_group(str(tmp_path), "g")
    assert n == 2
    f = ifcopenshell.open(path)
    assert sorted(t.Name for t in f.by_type("IfcDuctSilencerType")) == ["Box A", "Box B"]
    assert len(f.by_type("IfcDistributionPort")) == 2
