"""Mesh -> IFC library type: ports on faces, write a package, rebuild the group file."""

import math
import os
import re

import bmesh
import bpy
from bpy.props import StringProperty
from mathutils import Matrix, Vector

from .props import library_dir


def _mm(context):
    return 1000.0 * (context.scene.unit_settings.scale_length or 1.0)


def type_source(obj):
    """The mesh object this type/port belongs to (a port empty points at its parent)."""
    if obj is None:
        return None
    if obj.type == "EMPTY" and obj.ifclib_port.is_port and obj.parent:
        return obj.parent
    return obj if obj.type == "MESH" else None


def ports_of(obj):
    return [c for c in obj.children if c.type == "EMPTY" and c.ifclib_port.is_port]


# --------------------------------------------------------------------------
# add port


def _face_frame(face, scale):
    """Centre, normal, direction of the longest edge and the scaled vertices of a bmesh face,
    in object coordinates with the object scale applied (rotation and location excluded)."""
    S = Vector(scale)
    pts = [v.co * S for v in face.verts]
    c = sum(pts, Vector()) / len(pts)
    n = Vector((face.normal.x / S.x, face.normal.y / S.y, face.normal.z / S.z)).normalized()
    k = len(pts)
    a, b = max(((pts[i], pts[(i + 1) % k]) for i in range(k)), key=lambda e: (e[1] - e[0]).length)
    x = b - a
    x = (x - n * x.dot(n)).normalized()
    return c, n, x, pts


def _face_size(pts, c, n, x):
    """round with diameter, or rect with width (along x) and height."""
    y = n.cross(x)
    us = [(p - c).dot(x) for p in pts]
    vs = [(p - c).dot(y) for p in pts]
    radii = [math.hypot(u, v) for u, v in zip(us, vs)]
    r = sum(radii) / len(radii)
    if len(pts) >= 12 and r > 0 and max(abs(q - r) for q in radii) < 0.03 * r:
        return "round", 2 * r, 0, 0
    return "rect", 0, max(us) - min(us), max(vs) - min(vs)


class IFCLIB_OT_add_port(bpy.types.Operator):
    """Add a port at the selected face (Edit Mode). Z of the port points out of the face"""
    bl_idname = "ifclib.add_port"
    bl_label = "Add Port on Face"
    bl_options = {"REGISTER", "UNDO"}

    name: StringProperty(name="Name", default="")

    @classmethod
    def poll(cls, context):
        o = context.active_object
        return o is not None and o.type == "MESH" and context.mode == "EDIT_MESH"

    def execute(self, context):
        obj = context.active_object
        bm = bmesh.from_edit_mesh(obj.data)
        faces = [f for f in bm.faces if f.select]
        if len(faces) != 1:
            self.report({"ERROR"}, "Select exactly one face")
            return {"CANCELLED"}
        face = faces[0]
        c, n, x, pts = _face_frame(face, obj.matrix_world.to_scale())
        shape, d, w, h = _face_size(pts, c, n, x)
        existing = ports_of(obj)
        name = self.name or ("Inlet" if not existing else "Outlet" if len(existing) == 1 else f"Port{len(existing) + 1}")
        bpy.ops.object.mode_set(mode="OBJECT")
        e = bpy.data.objects.new(f"Port {name}", None)
        e.empty_display_type = "SINGLE_ARROW"
        e.empty_display_size = max(d, w, h, 0.05) * 0.6
        context.collection.objects.link(e)
        e.parent = obj
        y = n.cross(x)
        local = Matrix.Translation(c) @ Matrix((x, y, n)).transposed().to_4x4()
        e.matrix_world = obj.matrix_world.normalized() @ local
        p = e.ifclib_port
        p.is_port = True
        p.shape = shape
        p.d, p.w, p.h = d, w, h
        p.flow = "SINK" if name == "Inlet" else "SOURCE" if name == "Outlet" else "SOURCEANDSINK"
        e["ifclib_name"] = name
        bpy.ops.object.mode_set(mode="EDIT")
        self.report({"INFO"}, f"Port {name}: {shape} {'Ø%.0f' % (d * _mm(context)) if shape == 'round' else '%.0fx%.0f' % (w * _mm(context), h * _mm(context))} mm")
        return {"FINISHED"}


# --------------------------------------------------------------------------
# write type


def _slug(s):
    return re.sub(r"[^\w.-]+", "-", s, flags=re.UNICODE).strip("-") or "type"


def mesh_parts(context, obj, mm):
    """Evaluated mesh in object coordinates with object scale applied, in mm, one part per material."""
    from .ifclibgen.package import Part
    deps = context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(deps)
    me = ev.to_mesh()
    try:
        scale = obj.matrix_world.to_scale()
        coords = [[v.co.x * scale.x * mm, v.co.y * scale.y * mm, v.co.z * scale.z * mm] for v in me.vertices]
        mirrored = scale.x * scale.y * scale.z < 0
        # closed if every edge has exactly two faces
        bm = bmesh.new()
        bm.from_mesh(me)
        closed = all(len(e.link_faces) == 2 for e in bm.edges)
        bm.free()
        by_mat = {}
        for poly in me.polygons:
            idx = list(poly.vertices)
            if mirrored:
                idx.reverse()
            by_mat.setdefault(poly.material_index, []).append((idx, []))
        parts, styles = [], {}
        for mi, faces in sorted(by_mat.items()):
            mat = obj.material_slots[mi].material if mi < len(obj.material_slots) else None
            style = None
            if mat is not None:
                style = mat.name
                r, g, b, a = mat.diffuse_color
                styles[style] = {"rgb": [round(r, 3), round(g, 3), round(b, 3)], "transparency": round(1 - a, 3)}
            used = sorted({i for f, _ in faces for i in f})
            remap = {g: k for k, g in enumerate(used)}
            parts.append(Part([coords[g] for g in used], [([remap[i] for i in f], []) for f, _ in faces],
                              closed and len(by_mat) == 1, style))
        return parts, styles
    finally:
        ev.to_mesh_clear()


def port_defs(obj, mm):
    """Ports in the type's coordinates: the object's axes and origin, object scale applied."""
    base = obj.matrix_world.normalized().inverted()
    out = []
    for e in ports_of(obj):
        m = base @ e.matrix_world.normalized()
        p = e.ifclib_port
        d = {"name": e.get("ifclib_name") or e.name.replace("Port ", ""),
             "at": [round(c * mm, 3) for c in m.translation],
             "dir": [round(c, 6) for c in m.col[2][:3]], "xdir": [round(c, 6) for c in m.col[0][:3]],
             "shape": p.shape, "flow": p.flow, "system": p.system}
        if p.shape == "round":
            d["d"] = round(p.d * mm, 2)
        else:
            d["w"], d["h"] = round(p.w * mm, 2), round(p.h * mm, 2)
        out.append(d)
    return out


def build_package(context, obj):
    from .ifclibgen.package import Package, TypeDef
    t = obj.ifclib_type
    mm = _mm(context)
    name = t.type_name.strip() or obj.name
    parts, styles = mesh_parts(context, obj, mm)
    if not parts:
        raise ValueError("The mesh has no faces")
    ifc = {"class": t.ifc_class, "predefined": t.predefined.strip() or "NOTDEFINED"}
    psets = {}
    if t.manufacturer or t.model:
        psets["Pset_ManufacturerTypeInformation"] = {
            k: v for k, v in (("Manufacturer", t.manufacturer), ("ModelLabel", t.model)) if v}
    group = _slug(t.group or "custom")
    td = TypeDef(name, parts, insertion=t.insertion, element_type=t.element_type.strip(),
                 ports=port_defs(obj, mm), psets=psets)
    return Package(f"blender/{group}/{_slug(name)}", ifc, t.manufacturer, group, styles, [td])


class IFCLIB_OT_write_type(bpy.types.Operator):
    """Write the active mesh as a type to the library folder and rebuild its group file"""
    bl_idname = "ifclib.write_type"
    bl_label = "Write to Library"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return type_source(context.active_object) is not None and context.mode == "OBJECT"

    def execute(self, context):
        from .ifclibgen.core import rebuild_group, stable_guid
        from .ifclibgen.package import save_package
        obj = type_source(context.active_object)
        try:
            pkg = build_package(context, obj)
        except Exception as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        lib = library_dir()
        folder = os.path.join(lib, "packages", pkg.group, _slug(pkg.types[0].name))
        save_package(pkg, folder)
        try:
            path, n = rebuild_group(lib, pkg.group)
        except Exception as e:
            self.report({"ERROR"}, f"Package saved to {folder}, but the library file failed: {e}")
            return {"CANCELLED"}
        guid = stable_guid(pkg.source, pkg.types[0].name)
        msg = f"{pkg.types[0].name} → {path} ({n} types in group)"
        if obj.ifclib_type.place_in_project:
            placed = place_in_project(self, context, obj, path, guid)
            if placed:
                msg += "; placed in project"
        self.report({"INFO"}, msg)
        return {"FINISHED"}


def place_in_project(op, context, obj, library_path, guid):
    """Append the type to the open project and put an occurrence where the mesh is."""
    try:
        import bonsai.tool as tool
        from bonsai.bim.ifc import IfcStore
    except ImportError:
        op.report({"WARNING"}, "Bonsai is not enabled: type written, not placed")
        return False
    ifc = tool.Ifc.get()
    if not ifc:
        op.report({"WARNING"}, "No IFC project open: type written, not placed")
        return False
    try:
        ifc.by_guid(guid)
        op.report({"WARNING"}, "This type is already in the project; it is not updated yet (planned: Update types)")
        return False
    except RuntimeError:
        pass
    bpy.ops.bim.select_library_file(filepath=library_path)
    lib_type = IfcStore.library_file.by_guid(guid)
    bpy.ops.bim.append_library_element(definition=lib_type.id(), prop_index=0)
    new_type = ifc.by_guid(guid)
    matrix = obj.matrix_world.normalized()
    bpy.ops.object.select_all(action="DESELECT")
    bpy.ops.bim.add_occurrence(relating_type_id=new_type.id())
    occ_obj = context.active_object
    if occ_obj is None or tool.Ifc.get_entity(occ_obj) is None:
        op.report({"WARNING"}, "Type appended, but the occurrence was not created")
        return False
    occ_obj.matrix_world = matrix
    context.view_layer.update()
    bpy.ops.bim.edit_object_placement(obj=occ_obj.name)
    obj.hide_set(True)
    for e in ports_of(obj):
        e.hide_set(True)
    return True


classes = [IFCLIB_OT_add_port, IFCLIB_OT_write_type]
