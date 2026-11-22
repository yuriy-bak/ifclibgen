"""Update types in the open project from the library, with a simple report list.

Check: compare project types with the library files (by GlobalId and content hash).
Update: replace changed types; occurrence ports are moved so connections stay.
Report: flat list "type - element - storey - what's wrong"; click a line (or Next)
to select and frame the element, tick "checked". Saved next to the .ifc file.
"""

import os

import bpy
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, IntProperty, PointerProperty, StringProperty
from mathutils import Matrix

from .props import library_dir


def _tool():
    try:
        import bonsai.tool as tool
    except ImportError:
        return None
    return tool if tool.Ifc.get() else None


def _report_file():
    tool = _tool()
    path = tool.Ifc.get_path() if tool else ""
    if not path:
        return ""
    from .ifclibgen.update import report_path
    return report_path(path)


def _save(context):
    from .ifclibgen.update import Row, save_report
    path = _report_file()
    s = context.scene.ifclib_update
    if not path or not len(s.rows):
        return
    rows = [Row(r.type_name, r.element_guid, r.element_name, r.storey, r.issue, r.checked) for r in s.rows]
    try:
        save_report(path, rows)
    except OSError:
        pass


def _checked_changed(self, context):
    _save(context)


def _frame(context, obj):
    for o in context.selected_objects:
        o.select_set(False)
    obj.hide_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            if area.type != "VIEW_3D":
                continue
            region = next((r for r in area.regions if r.type == "WINDOW"), None)
            if region:
                with context.temp_override(window=window, area=area, region=region):
                    bpy.ops.view3d.view_selected()
                return


def _row_selected(self, context):
    s = context.scene.ifclib_update
    if not (0 <= s.active_row < len(s.rows)):
        return
    tool = _tool()
    if not tool:
        return
    try:
        el = tool.Ifc.get().by_guid(s.rows[s.active_row].element_guid)
    except RuntimeError:
        return
    obj = tool.Ifc.get_object(el)
    if obj is not None:
        _frame(context, obj)


class IFCLIB_TypeItem(bpy.types.PropertyGroup):
    type_id: IntProperty()
    guid: StringProperty()
    status: StringProperty()
    library: StringProperty()
    occurrences: IntProperty()
    ports: StringProperty()
    shift: StringProperty()
    keep_position: BoolProperty(
        name="Keep position in the model", default=True,
        description="The insertion point of the type changed. On: elements are moved so they stay where they are. "
                    "Off: elements keep their insertion point, the body shifts")


class IFCLIB_RowItem(bpy.types.PropertyGroup):
    type_name: StringProperty()
    element_guid: StringProperty()
    element_name: StringProperty()
    storey: StringProperty()
    issue: StringProperty()
    checked: BoolProperty(name="Checked", description="Mark as checked", update=_checked_changed)


class IFCLIB_UpdateProps(bpy.types.PropertyGroup):
    scope: EnumProperty(name="Types", items=[
        ("SELECTED", "Of selected", "Types of the selected elements"),
        ("ALL", "All", "All library types in the project")], default="ALL")
    types: CollectionProperty(type=IFCLIB_TypeItem)
    rows: CollectionProperty(type=IFCLIB_RowItem)
    active_row: IntProperty(update=_row_selected)
    hide_checked: BoolProperty(name="Hide checked", default=False)


def _selected_type_ids(tool, context):
    import ifcopenshell.util.element
    ids = set()
    for obj in context.selected_objects:
        el = tool.Ifc.get_entity(obj)
        if el is None:
            continue
        t = el if el.is_a("IfcTypeObject") else ifcopenshell.util.element.get_type(el)
        if t is not None:
            ids.add(t.id())
    return ids


class IFCLIB_OT_check_types(bpy.types.Operator):
    """Compare types in the project with the library files"""
    bl_idname = "ifclib.check_types"
    bl_label = "Check Library Types"

    def execute(self, context):
        from .ifclibgen.update import library_files, plan_update
        tool = _tool()
        if not tool:
            self.report({"ERROR"}, "Bonsai is not enabled or no IFC project open")
            return {"CANCELLED"}
        s = context.scene.ifclib_update
        ids = _selected_type_ids(tool, context) if s.scope == "SELECTED" else None
        if ids is not None and not ids:
            self.report({"ERROR"}, "No typed IFC elements selected")
            return {"CANCELLED"}
        files = library_files(library_dir())
        plans = plan_update(tool.Ifc.get(), files, ids)
        s.types.clear()
        for p in sorted(plans, key=lambda p: ({"changed": 0, "missing": 1}.get(p.status, 2), p.name)):
            it = s.types.add()
            it.name, it.type_id, it.guid, it.status = p.name, p.type_id, p.guid, p.status
            it.library, it.occurrences = p.library, p.occurrences
            it.ports = ", ".join(f"{c.name} {c.kind}" for c in p.ports if c.kind != "same")
            if p.shift:
                it.shift = " ".join(f"{c:g}" for c in p.shift)
        n = {k: sum(1 for p in plans if p.status == k) for k in ("changed", "unchanged", "missing")}
        self.report({"INFO"}, f"{n['changed']} changed, {n['unchanged']} up to date, {n['missing']} not in library "
                              f"({len(files)} library file(s))")
        return {"FINISHED"}


class IFCLIB_OT_update_types(bpy.types.Operator):
    """Replace changed types with the library version. Element ports are moved, connections stay"""
    bl_idname = "ifclib.update_types"
    bl_label = "Update Types"
    bl_options = {"REGISTER", "UNDO"}
    transaction_key = ""
    transaction_data = None

    @classmethod
    def poll(cls, context):
        if not any(t.status == "changed" for t in context.scene.ifclib_update.types):
            cls.poll_message_set("Nothing to update: press Check first")
            return False
        return True

    def execute(self, context):
        try:
            from bonsai.bim.ifc import IfcStore
        except ImportError:
            self.report({"ERROR"}, "Bonsai is not enabled")
            return {"CANCELLED"}
        self._result = {"FINISHED"}
        IfcStore.execute_ifc_operator(self, context)
        return self._result

    def _execute(self, context):
        self._result = self.run(context)
        return self._result

    def run(self, context):
        import ifcopenshell.util.element
        import ifcopenshell.util.unit
        from .ifclibgen.update import apply_update, plan_update, save_report
        tool = _tool()
        if not tool:
            self.report({"ERROR"}, "No IFC project open")
            return {"CANCELLED"}
        f = tool.Ifc.get()
        s = context.scene.ifclib_update
        items = {t.type_id: t for t in s.types if t.status == "changed"}
        plans = [p for p in plan_update(f, sorted({t.library for t in items.values()}), set(items))
                 if p.status == "changed"]
        unit = ifcopenshell.util.unit.calculate_unit_scale(f)

        # Blender side: port objects are dropped and loaded again after, moved elements follow
        moves, port_hosts, objs = [], [], []
        for p in plans:
            p.keep_position = items[p.type_id].keep_position
            t = f.by_id(p.type_id)
            elements = [t] + list(ifcopenshell.util.element.get_types(t))
            for el in elements:
                obj = tool.Ifc.get_object(el)
                if obj is not None:
                    objs.append(obj)
                ports = [q for q in tool.System.get_ports(el) if tool.Ifc.get_object(q)]
                if ports:
                    port_hosts.append(el)
                    tool.System.delete_element_objects(ports)
                if p.shift and p.keep_position and obj is not None and el is not t:
                    moves.append((obj, Matrix.Translation([-c * unit for c in p.shift])))

        rows = apply_update(f, plans)

        for obj, back in moves:
            obj.matrix_world = obj.matrix_world @ back
            tool.Geometry.record_object_position(obj)
        if objs:
            tool.Geometry.reload_representation(objs)
        for el in port_hosts:
            tool.System.load_ports(el, tool.System.get_ports(el))

        s.rows.clear()
        for r in rows:
            it = s.rows.add()
            it.type_name, it.element_guid, it.element_name = r.type_name, r.element_guid, r.element_name
            it.storey, it.issue = r.storey, r.issue
        for it in s.types:
            if it.type_id in items:
                it.status, it.ports, it.shift = "updated", "", ""
        path = _report_file()
        if path:
            try:
                save_report(path, rows, plans)
            except OSError:
                pass
        msg = f"Updated {len(plans)} type(s)"
        if rows:
            self.report({"WARNING"}, f"{msg}, {len(rows)} thing(s) to check, see the list")
        else:
            self.report({"INFO"}, msg + ", nothing to check")
        return {"FINISHED"}


class IFCLIB_OT_next_row(bpy.types.Operator):
    """Select and frame the next unchecked element of the report"""
    bl_idname = "ifclib.next_row"
    bl_label = "Next"

    @classmethod
    def poll(cls, context):
        return any(not r.checked for r in context.scene.ifclib_update.rows)

    def execute(self, context):
        s = context.scene.ifclib_update
        n = len(s.rows)
        for k in range(1, n + 1):
            i = (s.active_row + k) % n
            if not s.rows[i].checked:
                s.active_row = i  # selects and frames through the update callback
                return {"FINISHED"}
        return {"CANCELLED"}


class IFCLIB_OT_load_report(bpy.types.Operator):
    """Load the last update report saved next to the .ifc file"""
    bl_idname = "ifclib.load_report"
    bl_label = "Load Last Report"

    @classmethod
    def poll(cls, context):
        path = _report_file()
        return bool(path) and os.path.exists(path)

    def execute(self, context):
        from .ifclibgen.update import load_report
        s = context.scene.ifclib_update
        s.rows.clear()
        for r in load_report(_report_file()):
            it = s.rows.add()
            it.type_name, it.element_guid, it.element_name = r.type_name, r.element_guid, r.element_name
            it.storey, it.issue, it.checked = r.storey, r.issue, r.checked
        return {"FINISHED"}


class IFCLIB_UL_rows(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, "checked", text="")
        where = f" · {item.storey}" if item.storey else ""
        row.label(text=f"{item.type_name} · {item.element_name or item.element_guid}{where}: {item.issue}")

    def filter_items(self, context, data, propname):
        rows = getattr(data, propname)
        flags = [self.bitflag_filter_item] * len(rows)
        if data.hide_checked:
            flags = [0 if r.checked else self.bitflag_filter_item for r in rows]
        return flags, []


class IFCLIB_PT_update(bpy.types.Panel):
    bl_label = "Update types from library"
    bl_idname = "IFCLIB_PT_update"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "IFC Lib"
    bl_parent_id = "IFCLIB_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        s = context.scene.ifclib_update
        row = layout.row(align=True)
        row.prop(s, "scope", expand=True)
        layout.operator("ifclib.check_types", icon="FILE_REFRESH")

        changed = [t for t in s.types if t.status == "changed"]
        if len(s.types):
            other = {k: sum(1 for t in s.types if t.status == k) for k in ("unchanged", "missing", "updated")}
            info = [f"{v} {k.replace('unchanged', 'up to date').replace('missing', 'not in library')}"
                    for k, v in other.items() if v]
            layout.label(text=f"{len(changed)} changed" + (", " + ", ".join(info) if info else ""))
        for t in changed:
            box = layout.box()
            box.label(text=f"{t.name} ({t.occurrences} in model)", icon="FILE_3D")
            if t.ports:
                box.label(text="Ports: " + t.ports)
            if t.shift:
                box.label(text="Insertion point moved: " + t.shift, icon="ERROR")
                box.prop(t, "keep_position")
        for t in s.types:
            if t.status == "missing":
                layout.label(text=f"{t.name}: not in library, left as is", icon="QUESTION")
        if changed:
            layout.operator("ifclib.update_types", icon="IMPORT")

        layout.separator()
        if len(s.rows):
            done = sum(1 for r in s.rows if r.checked)
            layout.label(text=f"To check: {len(s.rows) - done} of {len(s.rows)}")
            layout.template_list("IFCLIB_UL_rows", "", s, "rows", s, "active_row", rows=5)
            row = layout.row(align=True)
            row.operator("ifclib.next_row", icon="TRIA_RIGHT")
            row.prop(s, "hide_checked", toggle=True)
        layout.operator("ifclib.load_report", icon="FILE_TEXT")
        layout.label(text="Library: " + library_dir())


classes = [IFCLIB_TypeItem, IFCLIB_RowItem, IFCLIB_UpdateProps, IFCLIB_OT_check_types, IFCLIB_OT_update_types,
           IFCLIB_OT_next_row, IFCLIB_OT_load_report, IFCLIB_UL_rows, IFCLIB_PT_update]


def register():
    bpy.types.Scene.ifclib_update = PointerProperty(type=IFCLIB_UpdateProps)


def unregister():
    del bpy.types.Scene.ifclib_update
