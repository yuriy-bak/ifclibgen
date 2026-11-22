import bpy

from .mesh_type import ports_of, type_source
from .props import library_dir


def _is_ifc(obj):
    try:
        import bonsai.tool as tool
        return tool.Ifc.get_entity(obj) is not None
    except Exception:
        return False


class IFCLIB_PT_main(bpy.types.Panel):
    bl_label = "IFC Library"
    bl_idname = "IFCLIB_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "IFC Lib"

    def draw(self, context):
        layout = self.layout
        box = layout.box()
        box.label(text="Dock by ports", icon="SNAP_ON")
        col = box.column(align=True)
        col.label(text="1. Click the target")
        col.label(text="2. Shift+click the element to move")
        sel = context.selected_objects
        act = context.active_object
        if len(sel) == 2 and act in sel:
            other = next(o for o in sel if o != act)
            box.label(text=f"Move: {act.name}", icon="FORWARD")
            box.label(text=f"To: {other.name}", icon="PIVOT_CURSOR")
        else:
            box.label(text=f"Selected: {len(sel)} (need 2)", icon="INFO")
        box.operator("ifclib.dock", text="Dock")


class IFCLIB_PT_mesh_type(bpy.types.Panel):
    bl_label = "Mesh → IFC type"
    bl_idname = "IFCLIB_PT_mesh_type"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "IFC Lib"
    bl_parent_id = "IFCLIB_PT_main"

    def draw(self, context):
        layout = self.layout
        active = context.active_object
        obj = type_source(active)
        if obj is None or _is_ifc(obj):
            layout.label(text="Select a plain mesh (not an IFC element)")
            return
        t = obj.ifclib_type
        col = layout.column()
        col.prop(t, "type_name", text="Name")
        col.prop(t, "ifc_class")
        col.prop(t, "predefined")
        if t.predefined.strip().upper() == "USERDEFINED":
            col.prop(t, "element_type")
        col.prop(t, "manufacturer")
        col.prop(t, "model")
        col.prop(t, "group")
        col.prop(t, "insertion")
        col.label(text="Origin of the mesh = insertion point", icon="OBJECT_ORIGIN")

        box = layout.box()
        box.label(text="Ports", icon="EMPTY_SINGLE_ARROW")
        box.operator("ifclib.add_port", text="Add port on selected face")
        if context.mode != "EDIT_MESH":
            box.label(text="(Edit Mode, select one face)")
        for e in ports_of(obj):
            p = e.ifclib_port
            size = f"Ø{p.d * 1000:.0f}" if p.shape == "round" else f"{p.w * 1000:.0f}×{p.h * 1000:.0f}"
            row = box.row()
            row.label(text=f"{e.get('ifclib_name', e.name)}: {size}, {p.flow.lower()}")
            row.operator("object.select_pattern", text="", icon="RESTRICT_SELECT_OFF").pattern = e.name

        if active is not None and active.type == "EMPTY" and active.ifclib_port.is_port:
            pb = layout.box()
            pb.label(text=f"Port: {active.get('ifclib_name', active.name)}")
            p = active.ifclib_port
            pb.prop(p, "shape")
            if p.shape == "round":
                pb.prop(p, "d")
            else:
                pb.prop(p, "w")
                pb.prop(p, "h")
            pb.prop(p, "flow")
            pb.prop(p, "system")

        layout.prop(t, "place_in_project")
        layout.operator("ifclib.write_type", icon="EXPORT")
        layout.label(text="Library: " + library_dir())


classes = [IFCLIB_PT_main, IFCLIB_PT_mesh_type]
