"""Toolbar commands of the IFC Library workbench."""

import os

import FreeCAD as App
import FreeCADGui as Gui

from . import family

ICONS = os.path.join(os.path.dirname(__file__), "icons")


def _icon(name):
    return os.path.join(ICONS, name + ".svg")


def _msg(text, title="IFC Library", warn=False):
    from PySide import QtGui
    (QtGui.QMessageBox.warning if warn else QtGui.QMessageBox.information)(Gui.getMainWindow(), title, text)


class NewFamily:
    def GetResources(self):
        return {"Pixmap": _icon("family"), "MenuText": "New family",
                "ToolTip": "Add the LibFamily object and the Params / Sizes spreadsheets to this document"}

    def IsActive(self):
        return App.ActiveDocument is not None

    def Activated(self):
        doc = App.ActiveDocument
        doc.openTransaction("New family")
        fam = family.create_family(doc)
        doc.commitTransaction()
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(fam)
        App.Console.PrintMessage(
            "IFC Library: fill LibFamily properties; drive the model by expressions like =Params.W; "
            "list sizes in the Sizes sheet (row 1 = aliases).\n")


class AddPort:
    def GetResources(self):
        return {"Pixmap": _icon("port"), "MenuText": "Port on face",
                "ToolTip": "Select the face of an opening (or of a flange ring), then click: adds a port attached "
                           "to it. Arrow = flow out of the body."}

    def IsActive(self):
        return bool(App.ActiveDocument and Gui.Selection.getSelectionEx())

    def Activated(self):
        doc = App.ActiveDocument
        sel = [(s.Object, n) for s in Gui.Selection.getSelectionEx() for n in s.SubElementNames if n.startswith("Face")]
        if not sel:
            _msg("Select one face first", warn=True)
            return
        doc.openTransaction("Port on face")
        made = []
        for obj, sub in sel:
            try:
                made.append(family.make_port(doc, obj, sub))
            except Exception as e:
                App.Console.PrintError(f"IFC Library: {obj.Label}.{sub}: {e}\n")
        doc.commitTransaction()
        for p in made:
            try:
                d = family.port_def(p)
                size = f"Ø{d['d']:g}" if d["shape"] == "round" else f"{d['w']:g}×{d['h']:g}"
                App.Console.PrintMessage(f"IFC Library: port {p.PortName}: {size} mm\n")
            except Exception as e:
                App.Console.PrintWarning(f"IFC Library: {p.Label}: {e}\n")
        if made:
            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(made[-1])


class CheckFamily:
    def GetResources(self):
        return {"Pixmap": _icon("check"), "MenuText": "Check family",
                "ToolTip": "Run every size: recompute, ports, names. Nothing is written."}

    def IsActive(self):
        return App.ActiveDocument is not None

    def Activated(self):
        res = family.check(App.ActiveDocument)
        for level, m in res:
            {"error": App.Console.PrintError, "warning": App.Console.PrintWarning}.get(
                level, App.Console.PrintMessage)(f"IFC Library: {m}\n")
        icon = {"error": "✗", "warning": "!", "ok": "✓"}
        _msg("\n".join(f"{icon[l]} {m}" for l, m in res[:40]), "Check family",
             warn=any(l == "error" for l, _ in res))


class ExportFamily:
    def GetResources(self):
        return {"Pixmap": _icon("export"), "MenuText": "Export family",
                "ToolTip": "Write all sizes to the library folder: package + <group>.ifc"}

    def IsActive(self):
        return App.ActiveDocument is not None

    def Activated(self):
        from PySide import QtGui
        doc = App.ActiveDocument
        fam = family.find_family(doc)
        if fam is None:
            _msg("No LibFamily object: use 'New family' first", warn=True)
            return
        lib = family.library_dir()
        if not lib or not os.path.isdir(lib):
            lib = QtGui.QFileDialog.getExistingDirectory(Gui.getMainWindow(), "Library folder", lib or os.path.expanduser("~"))
            if not lib:
                return
            family.set_library_dir(lib)
        bar = QtGui.QProgressDialog("Exporting…", None, 0, 100, Gui.getMainWindow())
        bar.setWindowTitle("IFC Library")
        bar.setMinimumDuration(0)

        def progress(i, n, name):
            bar.setMaximum(n)
            bar.setValue(i)
            bar.setLabelText(name)
            QtGui.QApplication.processEvents()
        try:
            pkg, path, n = family.export(doc, fam, lib, progress)
        except Exception as e:
            bar.close()
            App.Console.PrintError(f"IFC Library: export failed: {e}\n")
            _msg(f"Export failed:\n{e}", warn=True)
            return
        bar.close()
        App.Console.PrintMessage(f"IFC Library: {len(pkg.types)} type(s) → {path} ({n} in group)\n")
        _msg(f"{len(pkg.types)} type(s) written.\n\n{path}\n({n} type(s) in group '{pkg.group}')\n\n"
             f"Library folder can be changed: Tools ▸ Edit parameters ▸ Mod/IfcLibGen/LibraryDir, "
             f"or clear it to be asked again.")


class SetLibraryDir:
    def GetResources(self):
        return {"MenuText": "Library folder…", "ToolTip": "Where packages and group .ifc files are written"}

    def IsActive(self):
        return True

    def Activated(self):
        from PySide import QtGui
        lib = QtGui.QFileDialog.getExistingDirectory(Gui.getMainWindow(), "Library folder",
                                                     family.library_dir() or os.path.expanduser("~"))
        if lib:
            family.set_library_dir(lib)
            App.Console.PrintMessage(f"IFC Library: library folder {lib}\n")


COMMANDS = [("IfcLib_NewFamily", NewFamily), ("IfcLib_AddPort", AddPort), ("IfcLib_Check", CheckFamily),
            ("IfcLib_Export", ExportFamily), ("IfcLib_LibraryDir", SetLibraryDir)]


def register():
    for name, cls in COMMANDS:
        Gui.addCommand(name, cls())
    return [n for n, _ in COMMANDS]
