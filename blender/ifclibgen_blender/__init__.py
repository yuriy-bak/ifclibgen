"""IFC Library Tools for Blender + Bonsai.

- Dock: moves the active IFC element so that one of its free ports meets a free
  port of the other selected element, and connects the two ports.
- Mesh -> IFC type: ports are placed on faces of a plain mesh, then the mesh is
  written as a type to a library folder (a package plus a rebuilt group .ifc
  file, same format as the ifclibgen command line), and optionally placed in
  the open project instead of the mesh.
- Update types from library: changed types are replaced in the project, element
  ports are moved (connections stay), a short report lists what to check.

The library core (ifclibgen) is bundled inside this add-on by blender/build.py.
Needs Bonsai for everything that touches the open IFC project.
"""

import bpy

from . import dock, mesh_type, props, ui, update_types

classes = []


def register():
    classes[:] = props.classes + dock.make_classes() + mesh_type.classes + ui.classes + update_types.classes
    for c in classes:
        bpy.utils.register_class(c)
    props.register()
    dock.register()
    update_types.register()


def unregister():
    update_types.unregister()
    dock.unregister()
    props.unregister()
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
    classes.clear()
