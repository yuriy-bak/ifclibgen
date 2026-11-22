"""Build the FreeCAD workbench zip with the ifclibgen core bundled inside.

    python freecad/build.py        # -> dist/IfcLibGen-<version>.zip

Unzip into the FreeCAD Mod folder (Windows: %APPDATA%\\FreeCAD\\v1-1\\Mod), restart FreeCAD.
"""
import os
import re
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WB = os.path.join(HERE, "IfcLibGen")
CORE = ["__init__.py", "core.py", "expr.py", "geometry.py", "package.py", "update.py"]


def main():
    version = re.search(r"<version>([^<]+)</version>", open(os.path.join(WB, "package.xml"), encoding="utf-8").read()).group(1)
    os.makedirs(os.path.join(ROOT, "dist"), exist_ok=True)
    out = os.path.join(ROOT, "dist", f"IfcLibGen-{version}.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for base, dirs, files in os.walk(WB):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for f in files:
                p = os.path.join(base, f)
                z.write(p, os.path.join("IfcLibGen", os.path.relpath(p, WB)))
        for f in CORE:
            z.write(os.path.join(ROOT, "ifclibgen", f), os.path.join("IfcLibGen", "ifclibgen", f))
        z.write(os.path.join(ROOT, "LICENSE"), os.path.join("IfcLibGen", "LICENSE"))
    print(out)


if __name__ == "__main__":
    main()
