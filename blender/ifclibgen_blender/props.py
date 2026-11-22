import os

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, PointerProperty, StringProperty

IFC_CLASSES = [
    ("IfcAirTerminalType", "Air terminal", "Diffuser, grille, valve"),
    ("IfcDamperType", "Damper", "Control, fire, check damper"),
    ("IfcDuctSilencerType", "Silencer", ""),
    ("IfcAirTerminalBoxType", "Air terminal box", "VAV/CAV box"),
    ("IfcFanType", "Fan", ""),
    ("IfcFilterType", "Filter", ""),
    ("IfcAirToAirHeatRecoveryType", "Heat recovery", ""),
    ("IfcCoilType", "Coil", ""),
    ("IfcUnitaryEquipmentType", "Unitary equipment", "AHU, split unit"),
    ("IfcFlowMeterType", "Flow meter", ""),
    ("IfcDuctFittingType", "Duct fitting", "Plenum box, transition, tee"),
    ("IfcDuctSegmentType", "Duct segment", ""),
    ("IfcBuildingElementProxyType", "Proxy", "Anything else"),
]

FLOWS = [
    ("SINK", "In (sink)", "Air flows into the element"),
    ("SOURCE", "Out (source)", "Air flows out of the element"),
    ("SOURCEANDSINK", "Both", ""),
]

SYSTEMS = [
    ("supply", "Supply", ""),
    ("return", "Return", ""),
    ("exhaust", "Exhaust", ""),
    ("air", "Air (other)", ""),
]


class IFCLIB_Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    library_dir: StringProperty(
        name="Library folder",
        subtype="DIR_PATH",
        description="Where packages and group .ifc files are written. Empty: Bonsai's libraries folder, "
                    "so the files show up in Project Library",
    )

    def draw(self, context):
        self.layout.prop(self, "library_dir")
        self.layout.label(text="Now: " + library_dir())


def library_dir():
    prefs = bpy.context.preferences.addons.get(__package__)
    path = prefs.preferences.library_dir if prefs and prefs.preferences else ""
    if path:
        return bpy.path.abspath(path)
    try:
        import bonsai.tool as tool
        return str(tool.Blender.get_user_data_dir() / "libraries")
    except Exception:
        return os.path.join(os.path.expanduser("~"), "ifc-library")


class IFCLIB_TypeProps(bpy.types.PropertyGroup):
    """Settings of a mesh that will become a library type."""
    ifc_class: EnumProperty(name="IFC class", items=IFC_CLASSES, default="IfcAirTerminalType")
    predefined: StringProperty(name="Predefined type", default="NOTDEFINED",
                               description="E.g. DIFFUSER, GRILLE, CONTROLDAMPER, FIREDAMPER; "
                                           "USERDEFINED to use Element type")
    element_type: StringProperty(name="Element type", description="Used with USERDEFINED")
    type_name: StringProperty(name="Name", description="Type name, e.g. 'Acme damper 600x400'")
    manufacturer: StringProperty(name="Manufacturer")
    model: StringProperty(name="Model", description="Model / article")
    group: StringProperty(name="Group", default="custom",
                          description="Library file the type goes to: <library folder>/<group>.ifc")
    insertion: StringProperty(name="Insertion point", description="Where the origin is, e.g. 'centre of the inlet'")
    place_in_project: BoolProperty(
        name="Put into project instead of the mesh", default=True,
        description="After writing, add the type to the open IFC project, place an occurrence where the mesh is "
                    "and hide the mesh")


class IFCLIB_PortProps(bpy.types.PropertyGroup):
    """An empty that marks a port of a future library type (Z = flow direction out of the body)."""
    is_port: BoolProperty(default=False)
    shape: EnumProperty(name="Shape", items=[("round", "Round", ""), ("rect", "Rectangular", "")])
    d: FloatProperty(name="Diameter", unit="LENGTH", min=0)
    w: FloatProperty(name="Width", unit="LENGTH", min=0, description="Along the empty's X")
    h: FloatProperty(name="Height", unit="LENGTH", min=0, description="Along the empty's Y")
    flow: EnumProperty(name="Flow", items=FLOWS, default="SOURCEANDSINK")
    system: EnumProperty(name="System", items=SYSTEMS, default="supply")


classes = [IFCLIB_Preferences, IFCLIB_TypeProps, IFCLIB_PortProps]


def register():
    bpy.types.Object.ifclib_type = PointerProperty(type=IFCLIB_TypeProps)
    bpy.types.Object.ifclib_port = PointerProperty(type=IFCLIB_PortProps)


def unregister():
    del bpy.types.Object.ifclib_port
    del bpy.types.Object.ifclib_type
