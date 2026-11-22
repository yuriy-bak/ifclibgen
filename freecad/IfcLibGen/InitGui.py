import FreeCADGui as Gui


class IfcLibGenWorkbench(Gui.Workbench):
    MenuText = "IFC Library"
    ToolTip = "Families of MEP types: ports on faces, sizes from a spreadsheet, export to an IFC library"

    def __init__(self):
        import os
        import libgen_fc
        self.__class__.Icon = os.path.join(os.path.dirname(libgen_fc.__file__), "icons", "workbench.svg")

    def Initialize(self):
        from libgen_fc import commands
        self.cmds = commands.register()
        self.appendToolbar("IFC Library", self.cmds)
        self.appendMenu("IFC Library", self.cmds)

    def GetClassName(self):
        return "Gui::PythonWorkbench"


Gui.addWorkbench(IfcLibGenWorkbench())
