"""Dock: put the active element's free port onto a free port of the other selected element."""

import math

import bpy
import numpy as np
from bpy.props import BoolProperty, EnumProperty, IntProperty
from mathutils import Matrix


def _axis2(a):
    o = np.array(a.Location.Coordinates, dtype=float)
    o = np.pad(o, (0, 3 - len(o)))
    z = np.array(a.Axis.DirectionRatios if getattr(a, "Axis", None) else (0, 0, 1), dtype=float)
    x = np.array(a.RefDirection.DirectionRatios if getattr(a, "RefDirection", None) else (1, 0, 0), dtype=float)
    z /= np.linalg.norm(z)
    x = x - z * x.dot(z)
    x /= np.linalg.norm(x)
    m = np.eye(4)
    m[:3, 0], m[:3, 1], m[:3, 2], m[:3, 3] = x, np.cross(z, x), z, o
    return m


def _placement(p):
    """Absolute IfcLocalPlacement matrix in file units. Own code: the IfcOpenShell helper
    was renamed in 0.9.1 and its unit handling differs between versions."""
    if p is None:
        return np.eye(4)
    m = _axis2(p.RelativePlacement)
    rel = getattr(p, "PlacementRelTo", None)
    return _placement(rel) @ m if rel is not None else m


def _port_size(port):
    import ifcopenshell.util.element
    ps = ifcopenshell.util.element.get_psets(port).get("Pset_DistributionPortTypeDuct", {})
    return ps.get("ConnectionSubType"), ps.get("NominalWidth"), ps.get("NominalHeight")


def _flows_match(a, b):
    a, b = a or "NOTDEFINED", b or "NOTDEFINED"
    if "SOURCEANDSINK" in (a, b) or "NOTDEFINED" in (a, b):
        return True
    return {a, b} == {"SOURCE", "SINK"}


def candidate_pairs(tool, moving, target, unit, any_port=False):
    """Port pairs, best first: same shape and size, opposite flow, then nearest.

    Normally only free ports, plus ports already connected between these two
    elements (re-docking after a manual move). any_port=True takes every port;
    the chosen ones get disconnected from whatever they were connected to."""
    def usable(el, other):
        out = []
        for p in tool.System.get_ports(el):
            c = tool.System.get_connected_port(p)
            if any_port or c is None or c in tool.System.get_ports(other):
                out.append(p)
        return out
    pairs = []
    for pm in usable(moving, target):
        mm = _placement(pm.ObjectPlacement)
        sm = _port_size(pm)
        for pt in usable(target, moving):
            mt = _placement(pt.ObjectPlacement)
            st = _port_size(pt)
            same = sm[0] == st[0] and all(
                a is None or b is None or abs(a - b) < 1.0 for a, b in zip(sm[1:], st[1:]))
            dist = float(np.linalg.norm((mm[:3, 3] - mt[:3, 3]) * unit))
            score = (0 if same else 1, 0 if _flows_match(pm.FlowDirection, pt.FlowDirection) else 1, dist)
            pairs.append((score, pm, pt, same))
    pairs.sort(key=lambda x: x[0])
    return pairs


def _blender_matrix(m, unit):
    """IFC placement (project units) -> Blender world Matrix (metres)."""
    m = np.array(m, dtype=float)
    m[:3, 3] *= unit
    return Matrix(m.tolist())


def mt_pose(mp, mt, size_m, size_t, extra_deg=0):
    """New world pose of the moving port: at the target port, facing it, with the least turn.

    The element keeps its current orientation as far as possible: only the rotation
    that makes the two port axes opposite is applied. Round ports keep any twist
    around the axis; rectangular ones snap to the nearest twist where the sides line
    up (multiples of 180°, or 90° for a square / swapped sides)."""
    from mathutils import Quaternion, Vector
    rp = mp.to_quaternion()
    zp, xp = mp.col[2].xyz.normalized(), mp.col[0].xyz.normalized()
    zt, xt = mt.col[2].xyz.normalized(), mt.col[0].xyz.normalized()
    want = -zt
    if zp.dot(want) < -0.9999:  # pointing the same way: half turn, around world Z when possible
        axis = Vector((0, 0, 1))
        axis = (axis - zp * axis.dot(zp))
        if axis.length < 1e-6:
            axis = xp.copy()
        q = Quaternion(axis.normalized(), math.pi)
    else:
        q = zp.rotation_difference(want)
    x1 = q @ xp
    twist = 0.0
    if size_m[0] == "RECTANGULAR" and size_t[0] == "RECTANGULAR":
        wm, hm = size_m[1] or 0, size_m[2] or 0
        wt, ht = size_t[1] or 0, size_t[2] or 0
        straight = abs(wm - wt) < 1 and abs(hm - ht) < 1
        swapped = abs(wm - ht) < 1 and abs(hm - wt) < 1
        if straight and swapped:
            steps = [0, 90, 180, 270]
        elif straight:
            steps = [0, 180]
        elif swapped:
            steps = [90, 270]
        else:
            steps = [0, 90, 180, 270]
        ang = math.degrees(math.atan2(want.dot(x1.cross(xt)), x1.dot(xt)))
        best = min(steps, key=lambda a: abs((ang - a + 180) % 360 - 180))
        twist = math.radians(ang - best)
    q = Quaternion(want, twist + math.radians(extra_deg)) @ q
    pose = (q @ rp).to_matrix().to_4x4()
    pose.translation = mt.translation
    return pose


class DockMixin:
    """Move the active element so its free port meets a free port of the other selected element, and connect them"""
    bl_idname = "ifclib.dock"
    bl_label = "Dock by Ports"
    bl_options = {"REGISTER", "UNDO"}

    pair: IntProperty(name="Port pair", default=0, min=0,
                      description="0 = best match; increase to try the next pair")
    rotation: EnumProperty(name="Extra turn around port axis", items=[
        ("0", "0°", ""), ("90", "90°", ""), ("180", "180°", ""), ("270", "270°", "")], default="0")
    connect: BoolProperty(name="Connect ports", default=True)

    @classmethod
    def poll(cls, context):
        n = len(context.selected_objects)
        if n != 2 or context.active_object is None or not context.active_object.select_get():
            cls.poll_message_set(f"Select exactly 2 elements (now {n}): target, then Shift+click the one to move")
            return False
        return True

    def run(self, context):
        try:
            import bonsai.tool as tool
            import ifcopenshell.util.unit
        except ImportError:
            self.report({"ERROR"}, "Bonsai is not enabled")
            return {"CANCELLED"}
        ifc = tool.Ifc.get()
        if not ifc:
            self.report({"ERROR"}, "No IFC project open")
            return {"CANCELLED"}
        obj = context.active_object
        other = next(o for o in context.selected_objects if o != obj)
        moving, target = tool.Ifc.get_entity(obj), tool.Ifc.get_entity(other)
        if not moving or not target:
            self.report({"ERROR"}, "Both selected objects must be IFC elements")
            return {"CANCELLED"}
        for o in (obj, other):
            tool.Model.sync_object_ifc_position(o)
        unit = ifcopenshell.util.unit.calculate_unit_scale(ifc)
        pairs = candidate_pairs(tool, moving, target, unit)
        reused = False
        if not pairs:
            pairs = candidate_pairs(tool, moving, target, unit, any_port=True)
            reused = True
        if not pairs:
            self.report({"ERROR"}, "One of the elements has no ports")
            return {"CANCELLED"}
        score, pm, pt, same = pairs[min(self.pair, len(pairs) - 1)]

        w = _blender_matrix(_placement(moving.ObjectPlacement), unit)
        mp = _blender_matrix(_placement(pm.ObjectPlacement), unit)
        mt = _blender_matrix(_placement(pt.ObjectPlacement), unit)
        local = w.inverted() @ mp  # port in element coordinates
        new = mt_pose(mp, mt, _port_size(pm), _port_size(pt), int(self.rotation)) @ local.inverted()
        # keep Blender's object offset (if any) out of it: apply the change, not the absolute value
        obj.matrix_world = new @ w.inverted() @ obj.matrix_world
        context.view_layer.update()
        bpy.ops.bim.edit_object_placement(obj=obj.name)

        for p in (pm, pt):
            c = tool.System.get_connected_port(p)
            if c is not None and c not in (pm, pt):
                tool.Ifc.run("system.disconnect_port", port=p)
        if self.connect and tool.System.get_connected_port(pm) != pt:
            if tool.System.get_connected_port(pm) is not None:
                tool.Ifc.run("system.disconnect_port", port=pm)
            tool.Ifc.run("system.connect_port", port1=pm, port2=pt, direction=pm.FlowDirection or "NOTDEFINED")
        msg = f"Docked {pm.Name or 'port'} → {pt.Name or 'port'} ({len(pairs)} pair(s) possible)"
        warn = []
        if not same:
            warn.append("port sizes differ")
        if reused:
            warn.append("no free ports: used connected ones and reconnected them")
        if warn:
            self.report({"WARNING"}, msg + ". " + "; ".join(warn))
        else:
            self.report({"INFO"}, msg)
        return {"FINISHED"}


class IFCLIB_OT_dock(DockMixin, bpy.types.Operator):
    transaction_key = ""
    transaction_data = None

    def execute(self, context):
        # Run through Bonsai's IFC transaction, so Ctrl+Z also undoes the placement and the
        # port connection (same as Bonsai's own operators). Imported lazily: Bonsai may load later.
        try:
            from bonsai.bim.ifc import IfcStore
        except ImportError:
            return self.run(context)
        self._result = {"FINISHED"}
        IfcStore.execute_ifc_operator(self, context)
        return self._result

    def _execute(self, context):
        self._result = self.run(context) or {"FINISHED"}
        return self._result


IFCLIB_OT_dock.__doc__ = DockMixin.__doc__


def make_classes():
    return [IFCLIB_OT_dock]
