"""Build the Blender extension zip with the ifclibgen core bundled inside.

    python blender/build.py                      -> dist/ifclibgen_blender-<version>.zip
    python blender/build.py dist --index URL     -> also dist/index.json for a Blender remote
                                                    extension repository; URL = where the zip is served

Install: Blender > Edit > Preferences > Get Extensions > (menu) Install from Disk.
"""

import os
import hashlib
import json
import re
import shutil
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ADDON = os.path.join(HERE, "ifclibgen_blender")
CORE = os.path.join(ROOT, "ifclibgen")
CORE_FILES = ["__init__.py", "core.py", "expr.py", "geometry.py", "package.py"]


def stage(dest):
    """Addon folder with the core copied in as a subpackage."""
    if os.path.exists(dest):
        shutil.rmtree(dest)
    shutil.copytree(ADDON, dest, ignore=shutil.ignore_patterns("__pycache__", "ifclibgen"))
    os.makedirs(os.path.join(dest, "ifclibgen"))
    for fn in CORE_FILES:
        shutil.copy(os.path.join(CORE, fn), os.path.join(dest, "ifclibgen", fn))
    return dest


def _manifest():
    """Minimal TOML reader for the flat keys and the [permissions] table we use."""
    data = {}
    cur = data
    with open(os.path.join(ADDON, "blender_manifest.toml"), encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("[") and line.endswith("]"):
                cur = data.setdefault(line[1:-1], {})
                continue
            k, _, v = line.partition("=")
            cur[k.strip()] = json.loads(v.strip())
    return data


def write_index(zpath, url_base, out_dir):
    """index.json in the format of `blender --command extension server-generate`."""
    m = _manifest()
    with open(zpath, "rb") as fh:
        blob = fh.read()
    entry = {k: m[k] for k in ("id", "schema_version", "name", "version", "tagline", "maintainer", "type",
                               "blender_version_min", "license", "website", "tags", "permissions") if k in m}
    entry.update({
        "archive_url": url_base.rstrip("/") + "/" + os.path.basename(zpath),
        "archive_size": len(blob),
        "archive_hash": "sha256:" + hashlib.sha256(blob).hexdigest(),
    })
    path = os.path.join(out_dir, "index.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"version": "v1", "blocklist": [], "data": [entry]}, fh, ensure_ascii=False, indent=2)
    return path


def main(out_dir=None, index_url=None):
    out_dir = out_dir or os.path.join(ROOT, "dist")
    with open(os.path.join(ADDON, "blender_manifest.toml"), encoding="utf-8") as fh:
        version = re.search(r'^version\s*=\s*"([^"]+)"', fh.read(), re.M).group(1)
    tmp = stage(os.path.join(out_dir, "ifclibgen_blender"))
    zpath = os.path.join(out_dir, f"ifclibgen_blender-{version}.zip")
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for dirpath, _, files in os.walk(tmp):
            for fn in files:
                full = os.path.join(dirpath, fn)
                z.write(full, os.path.relpath(full, tmp))
    shutil.rmtree(tmp)
    print(zpath)
    if index_url:
        print(write_index(zpath, index_url, out_dir))
    return zpath


if __name__ == "__main__":
    args = sys.argv[1:]
    url = None
    if "--index" in args:
        i = args.index("--index")
        url = args[i + 1]
        del args[i:i + 2]
    main(args[0] if args else None, url)
