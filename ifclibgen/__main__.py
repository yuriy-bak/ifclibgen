"""Command line.

Build the whole library:
    python -m ifclibgen build -o out/library.ifc
Only some templates:
    python -m ifclibgen build -o out/x.ifc --only example/damper example/diffuser
File per family (data file, or its `file:` key):
    python -m ifclibgen build --split -o out/lib/
One type with given sizes:
    python -m ifclibgen one example/damper W=650 H=350 -o out/damper-650x350.ifc
Templates as package folders (the format FreeCAD/Blender write):
    python -m ifclibgen pack -o out/packages/
Build from package folders too (with or without templates):
    python -m ifclibgen build --split -o out/lib/ --packages out/packages/ --no-templates
Check a built file:
    python -m ifclibgen check out/library.ifc
"""

import argparse
import os
import sys

from . import core
from .expr import ExprError

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _kv(items):
    out = {}
    for it in items:
        k, _, v = it.partition("=")
        out[k] = core._num(v)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ifclibgen")
    ap.add_argument("--templates", default=os.path.join(HERE, "templates"))
    ap.add_argument("--data", default=os.path.join(HERE, "data"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("-o", "--out", required=True)
    b.add_argument("--only", nargs="*")
    b.add_argument("--name", default="Library")
    b.add_argument("--split", action="store_true", help="one file per data group; -o is a directory")
    b.add_argument("--chord", type=float, help="facet length on circles, mm (default 25)")
    b.add_argument("--packages", nargs="*", default=[], help="package folders (or folders containing them)")
    b.add_argument("--no-templates", action="store_true", help="only --packages, skip templates + data")
    k = sub.add_parser("pack")
    k.add_argument("-o", "--out", required=True)
    k.add_argument("--only", nargs="*")
    k.add_argument("--chord", type=float)
    o = sub.add_parser("one")
    o.add_argument("template")
    o.add_argument("params", nargs="*")
    o.add_argument("-o", "--out", required=True)
    c = sub.add_parser("check")
    c.add_argument("file")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "build":
            data = None if a.no_templates else a.data
            written, report = core.build_library(a.templates, data, a.out, a.only, a.name, a.split, a.chord,
                                                 a.packages)
            for src, n in report:
                print(f"{src:28s} {n:4d} types")
            for path, n in written:
                print(f"{n:4d} types -> {path}")
        elif a.cmd == "pack":
            for path, n in core.export_packages(a.templates, a.data, a.out, a.only, a.chord):
                print(f"{n:4d} types -> {path}")
        elif a.cmd == "one":
            lib, elem = core.build_one(a.templates, a.template, _kv(a.params), a.out)
            print(f"{elem.Name} -> {a.out}")
        elif a.cmd == "check":
            from .check import check_file
            problems = check_file(a.file)
            for p in problems:
                print(p)
            print(f"{len(problems)} problem(s)")
            return 1 if problems else 0
    except ExprError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
