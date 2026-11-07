#!/usr/bin/env python3
"""Offline Cocos Studio preview and legacy JSON property editor."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys

if "--export" in sys.argv:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import Qt, QTimer, QEvent, QStandardPaths
from PyQt5 import sip
from PyQt5.QtGui import QColor, QPainter, QKeySequence
from PyQt5.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog, QGraphicsView,
                            QHBoxLayout, QLabel, QListWidget, QMainWindow, QMessageBox,
                            QPlainTextEdit, QPushButton, QSplitter, QTreeWidget,
                            QTreeWidgetItem, QVBoxLayout, QWidget, QTabWidget)

from .model import Document
from .scene import PreviewScene
from .editor import EditorSession
from .inspector import PropertyInspector
from . import __version__


def sample_path():
    return Path(__file__).resolve().parent / "examples/demo.json"


def working_demo(directory=None):
    """Create a user-owned example; never edit a frozen bundle's resources."""
    directory = Path(directory or QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)) / "Editable demo"
    directory.mkdir(parents=True, exist_ok=True)
    for source in sample_path().parent.iterdir():
        target = directory / source.name
        if source.is_file() and not target.exists():
            shutil.copyfile(source, target)
    return directory / "demo.json"


class PreviewApplication(QApplication):
    """Receive Finder Open With / dropped-document events after launch."""
    window = None
    pending_file = None

    def event(self, event):
        if event.type() == QEvent.FileOpen and event.file():
            if self.window:
                self.window.open_path(Path(event.file()))
                self.window.show(); self.window.raise_(); self.window.activateWindow()
            else:
                self.pending_file = Path(event.file())
            return True
        return super().event(event)


class Canvas(QGraphicsView):
    def __init__(self):
        super().__init__()
        self.setDragMode(self.ScrollHandDrag)
        self.setTransformationAnchor(self.AnchorUnderMouse)
        self.setRenderHint(QPainter.TextAntialiasing)
        self.setBackgroundBrush(QColor("#18212b"))
        self.setStyleSheet("border: 0")

    def wheelEvent(self, event):
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        next_scale = self.transform().m11() * factor
        if .02 <= next_scale <= 32:
            self.scale(factor, factor)
        event.accept()

    def fit(self):
        if self.scene():
            self.fitInView(self.scene().sceneRect().adjusted(-20, -20, 20, 20), Qt.KeepAspectRatio)


class PreviewWindow(QMainWindow):
    def __init__(self, path=None, resource_root=None):
        super().__init__()
        self.resource_root = resource_root
        self.scene = None
        self.session = None
        self.tree_items = {}
        self.setWindowTitle("Cocos UI Preview")
        self.resize(1580, 980)
        self.setAcceptDrops(True)
        main = QWidget(); self.setCentralWidget(main)
        layout = QVBoxLayout(main)
        toolbar = QHBoxLayout(); layout.addLayout(toolbar)
        open_button = QPushButton("Open UI…"); open_button.clicked.connect(self.open_file); toolbar.addWidget(open_button)
        root_button = QPushButton("Resource root…"); root_button.clicked.connect(self.choose_resource_root); toolbar.addWidget(root_button)
        self.files = QComboBox(); self.files.setEditable(True); self.files.setInsertPolicy(QComboBox.NoInsert)
        self.files.setMinimumWidth(250); self.files.activated.connect(self.choose_file); toolbar.addWidget(self.files, 1)
        for title, action in [("Reload", self.reload), ("Fit", lambda: self.view.fit()),
                              ("100%", lambda: self.view.resetTransform()), ("Export PNG…", self.export_png)]:
            button = QPushButton(title); button.clicked.connect(action); toolbar.addWidget(button)
        self.panel_fill = QCheckBox("Panel fill"); self.panel_fill.setChecked(True)
        self.hidden = QCheckBox("Show hidden")
        self.bounds = QCheckBox("Bounds")
        self.isolate = QCheckBox("Isolate selection")
        for control in [self.panel_fill, self.hidden, self.bounds, self.isolate]:
            toolbar.addWidget(control); control.toggled.connect(self.update_options)
        self.summary = QLabel("Open Cocos Studio JSON/CSD. Scroll to zoom; drag to pan.")
        editing = QHBoxLayout(); layout.addLayout(editing)
        self.save_button = QPushButton("Save"); self.save_button.clicked.connect(self.save_file)
        self.save_as_button = QPushButton("Save As…"); self.save_as_button.clicked.connect(self.save_as)
        self.undo_button = QPushButton("Undo"); self.undo_button.clicked.connect(lambda: self.session and self.session.undo.undo())
        self.redo_button = QPushButton("Redo"); self.redo_button.clicked.connect(lambda: self.session and self.session.undo.redo())
        for button in [self.save_button, self.save_as_button, self.undo_button, self.redo_button]:
            editing.addWidget(button)
        editing.addWidget(QLabel("Legacy JSON editor · Studio JSON/CSD preview")); editing.addStretch()
        layout.addWidget(self.summary)
        splitter = QSplitter(); layout.addWidget(splitter, 1)
        self.tree = QTreeWidget(); self.tree.setHeaderLabels(["Node", "Type"])
        self.tree.setColumnWidth(0, 190); self.tree.currentItemChanged.connect(self.select_tree)
        splitter.addWidget(self.tree)
        self.view = Canvas(); splitter.addWidget(self.view)
        inspector = QWidget(); right = QVBoxLayout(inspector); right.setContentsMargins(4, 0, 0, 0)
        self.inspector = PropertyInspector()
        self.inspector.edited.connect(self.edit_property); self.inspector.reset.connect(self.reset_property)
        self.properties = QPlainTextEdit(); self.properties.setReadOnly(True)
        property_tabs = QTabWidget(); property_tabs.addTab(self.inspector, "Edit properties")
        property_tabs.addTab(self.properties, "Computed / raw · read only"); right.addWidget(property_tabs, 4)
        right.addWidget(QLabel("Resources and preview limits"))
        self.issues = QListWidget(); self.issues.setWordWrap(True); right.addWidget(self.issues, 1)
        splitter.addWidget(inspector); splitter.setSizes([240, 870, 470])
        self.statusBar().showMessage("Static template · runtime Lua, actors and native text input are not rendered")
        self.addAction(self.make_shortcut("Open", QKeySequence.Open, self.open_file))
        file_menu = self.menuBar().addMenu("File")
        file_menu.addAction(self.make_shortcut("Save", QKeySequence.Save, self.save_file))
        file_menu.addAction(self.make_shortcut("Save As…", QKeySequence.SaveAs, self.save_as))
        edit_menu = self.menuBar().addMenu("Edit")
        edit_menu.addAction(self.make_shortcut("Undo property", QKeySequence.Undo, lambda: self.session and self.session.undo.undo()))
        edit_menu.addAction(self.make_shortcut("Redo property", QKeySequence.Redo, lambda: self.session and self.session.undo.redo()))
        menu = self.menuBar().addMenu("Help")
        menu.addAction("About Cocos UI Preview", self.about)
        self.root_label = QLabel(); layout.insertWidget(1, self.root_label)
        self.update_root_label()
        self.update_edit_state()
        if path:
            path = Path(path)
            directory = path if path.is_dir() else path.parent
            self.fill_files(directory)
            initial = Path(self.files.itemData(0)) if path.is_dir() and self.files.count() else path
            if not initial.is_file() and self.files.count():
                initial = Path(self.files.itemData(0))
            if initial.is_file():
                self.load_file(initial)

    def make_shortcut(self, title, key, callback):
        from PyQt5.QtWidgets import QAction
        action = QAction(title, self); action.setShortcut(key); action.triggered.connect(callback); return action

    def fill_files(self, directory):
        self.files.clear()
        for path in sorted(p for p in Path(directory).iterdir() if p.suffix.lower() in {".json", ".csd"} and p.is_file()):
            self.files.addItem(path.name, str(path.resolve()))
        self.files.completer().setCaseSensitivity(Qt.CaseInsensitive)
        self.files.completer().setFilterMode(Qt.MatchContains)

    def choose_file(self, index):
        path = self.files.itemData(index)
        if path:
            self.load_file(Path(path))

    def open_file(self):
        directory = self.scene.document.path.parent if self.scene else Path.home()
        path, _ = QFileDialog.getOpenFileName(self, "Open Cocos Studio UI", str(directory), "Cocos Studio UI (*.json *.csd)")
        if path:
            self.open_path(Path(path))

    def open_path(self, path):
        if not self.load_file(path):
            return False
        self.fill_files(Path(path).parent)
        self.files.setCurrentIndex(self.files.findData(str(Path(path).resolve())))
        return True

    def update_root_label(self):
        self.root_label.setText(f"Resource root: {self.resource_root}" if self.resource_root else
                                "Resource root: document directory + inferred res/Resources ancestor. Choose a root if assets are missing.")

    def choose_resource_root(self):
        path = QFileDialog.getExistingDirectory(self, "Choose project resource root", str(self.resource_root or Path.home()))
        if path:
            self.resource_root = Path(path)
            self.update_root_label(); self.refresh_preview()

    def about(self):
        QMessageBox.about(self, "Cocos UI Preview", f"Cocos UI Preview {__version__}\n"
            "Legacy JSON property editor; Studio JSON/CSD preview.\nGPL-3.0-only · PyQt5 / Qt / Pillow\n"
            "https://github.com/minhnndev/cocos-ui-preview\n\n"
            "No Lua execution, native game runtime or CSB decoder.\n"
            "Original demo assets are included; your game assets stay local.")

    def reload(self):
        if self.scene:
            self.load_file(self.scene.document.path)

    def load_file(self, path):
        if not self.confirm_unsaved():
            if self.scene:
                self.files.setCurrentIndex(self.files.findData(str(self.scene.document.path)))
            return False
        try:
            new_scene = PreviewScene(Document.load(path), self.resource_root)
        except (ValueError, OSError, TypeError, KeyError, OverflowError, RecursionError) as error:
            QMessageBox.warning(self, "Cannot preview file", str(error)); return False
        previous = self.scene
        if previous:
            previous.selectionChanged.disconnect(self.select_canvas)
        self.scene = new_scene; self.view.setScene(new_scene)
        self.scene.selectionChanged.connect(self.select_canvas)
        if self.session:
            self.session.changed.disconnect(self.refresh_preview)
            self.session.undo.indexChanged.disconnect(self.update_edit_state)
            self.session.undo.cleanChanged.disconnect(self.update_edit_state)
            self.session.deleteLater()
        self.session = EditorSession(new_scene.document, self.validate_preview)
        self.session.changed.connect(self.refresh_preview)
        self.session.undo.indexChanged.connect(self.update_edit_state)
        self.session.undo.cleanChanged.connect(self.update_edit_state)
        self.tree.blockSignals(True); self.tree.clear(); self.tree_items = {}

        def insert(node, parent=None):
            item = QTreeWidgetItem([node.name, node.kind]); item.setData(0, Qt.UserRole, node.uid)
            self.tree_items[node.uid] = item
            if parent is None:
                self.tree.addTopLevelItem(item)
            else:
                parent.addChild(item)
            for child in node.children:
                insert(child, item)

        insert(new_scene.document.root); self.tree.expandToDepth(0); self.tree.blockSignals(False)
        self.properties.clear(); self.inspector.clear(); self.isolate.setChecked(False)
        index = self.files.findData(str(new_scene.document.path))
        if index >= 0:
            self.files.setCurrentIndex(index)
        self.update_edit_state()
        self.update_options(); QTimer.singleShot(0, self.view.fit)
        if previous:
            previous.deleteLater()
        return True

    def validate_preview(self, document):
        scene = PreviewScene(document, self.resource_root)
        scene.deleteLater()

    def refresh_preview(self):
        if not self.session:
            return
        previous = self.scene
        selected = self.tree.currentItem()
        uid = selected.data(0, Qt.UserRole) if selected else None
        transform = self.view.transform()
        x, y = self.view.horizontalScrollBar().value(), self.view.verticalScrollBar().value()
        scene = PreviewScene(self.session.document, self.resource_root)
        previous.selectionChanged.disconnect(self.select_canvas)
        self.scene = scene; self.view.setScene(scene)
        scene.selectionChanged.connect(self.select_canvas)
        self.view.setTransform(transform)
        self.view.horizontalScrollBar().setValue(x); self.view.verticalScrollBar().setValue(y)
        for node in scene.document.root.walk():
            self.tree_items[node.uid].setText(0, node.name)
        if uid:
            scene.blockSignals(True); scene.node_items[uid].setSelected(True); scene.blockSignals(False)
        self.update_options()
        if uid:
            self.inspect(scene.node_items[uid])
        previous.deleteLater()

    def update_edit_state(self, *args):
        if sip.isdeleted(self) or sip.isdeleted(self.save_button):
            return
        valid = self.session is not None and not sip.isdeleted(self.session) and not sip.isdeleted(self.session.undo)
        editable = bool(valid and self.session.editable)
        self.save_button.setEnabled(editable and self.session.dirty)
        self.save_as_button.setEnabled(editable)
        self.undo_button.setEnabled(editable and self.session.undo.canUndo())
        self.redo_button.setEnabled(editable and self.session.undo.canRedo())
        if valid:
            self.setWindowTitle(f"{'* ' if self.session.dirty else ''}{self.session.document.path.name} · Cocos UI Preview")

    def edit_property(self, key, value):
        if not self.session or not self.inspector.uid:
            return False
        try:
            return self.session.edit(self.inspector.uid[0], key, value)
        except (ValueError, OSError, TypeError, KeyError, OverflowError) as error:
            QMessageBox.warning(self, "Cannot edit property", str(error))
            self.inspect(self.scene.node_items[self.inspector.uid[0]])
            return False

    def reset_property(self, key):
        if self.session and self.inspector.uid:
            try:
                self.session.edit(self.inspector.uid[0], key, reset=True)
            except (ValueError, OSError, TypeError, KeyError, OverflowError) as error:
                QMessageBox.warning(self, "Cannot reset property", str(error))

    def save_file(self, checked=False, destination=None):
        if self.focusWidget():
            self.focusWidget().clearFocus()  # Commit pending spinbox/line input before serializing.
        if not self.session or not self.session.editable:
            return False
        try:
            path = self.session.save(destination)
        except (ValueError, OSError, TypeError) as error:
            QMessageBox.warning(self, "Cannot save template", str(error)); return False
        self.fill_files(path.parent); self.files.setCurrentIndex(self.files.findData(str(path)))
        self.update_edit_state()
        self.statusBar().showMessage(f"Saved {path}")
        return True

    def save_as(self):
        if not self.session or not self.session.editable:
            return False
        source = self.session.document.path
        path, _ = QFileDialog.getSaveFileName(self, "Save As · new filename in the source folder", str(source.with_name(source.stem + "-edited.json")), "Legacy JSON (*.json)")
        return self.save_file(destination=path) if path else False

    def confirm_unsaved(self):
        if self.focusWidget():
            self.focusWidget().clearFocus()
        if not self.session or not self.session.dirty:
            return True
        choice = QMessageBox.warning(self, "Unsaved template changes", "Save your changes before leaving this document?",
                                     QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Cancel)
        if choice == QMessageBox.Save:
            return self.save_file()
        return choice == QMessageBox.Discard

    def update_options(self):
        if not self.scene:
            return
        self.scene.panel_fill = self.panel_fill.isChecked()
        self.scene.show_hidden = self.hidden.isChecked()
        self.scene.bounds = self.bounds.isChecked()
        selected = self.tree.currentItem()
        self.scene.isolated = selected.data(0, Qt.UserRole) if self.isolate.isChecked() and selected else None
        self.scene.update_visibility()
        for uid, tree_item in self.tree_items.items():
            tree_item.setForeground(0, QColor("#d6dce6") if self.scene.node_items[uid].isVisible() else QColor("#8290a3"))
        report = self.scene.report()
        self.summary.setText(f"{self.scene.document.path.name}  ·  {int(self.scene.document.width)} × {int(self.scene.document.height)}  ·  "
                             f"{report['nodes']} nodes / {report['visible_nodes']} visible / {report['image_nodes']} textured  ·  Static template")
        self.issues.clear(); self.issues.addItems(report["issues"])

    def inspect(self, item):
        bounds = item.sceneBoundingRect()
        info = {"name": item.node.name, "type": item.node.kind, "node_id": item.node.uid,
                "visible_in_template": item.o.get("visible", True), "visible_with_ancestors": item.isVisible(),
                "content_size": [item.width, item.height],
                "texture_size": [item.image.width(), item.image.height()] if item.image is not None else None,
                "canvas_bounds_top_left": [round(v, 3) for v in [bounds.x(), bounds.y(), bounds.width(), bounds.height()]],
                "resource": item.reference, "options": item.o}
        self.properties.setPlainText(json.dumps(info, indent=2, ensure_ascii=False))
        self.inspector.bind(item.node, self.scene.document.path, self.session.editable, self.session.read_only_reason)

    def select_tree(self, current, previous=None):
        if not current or not self.scene:
            return
        item = self.scene.node_items[current.data(0, Qt.UserRole)]
        self.scene.blockSignals(True); self.scene.clearSelection(); item.setSelected(True); self.scene.blockSignals(False)
        self.inspect(item)
        if self.isolate.isChecked():
            self.update_options()

    def select_canvas(self):
        # Qt can emit selectionChanged while deleting a scene during app teardown.
        if self.scene is None or sip.isdeleted(self.scene):
            return
        selected = self.scene.selectedItems()
        if selected:
            item = selected[0]
            self.tree.setCurrentItem(self.tree_items[item.node.uid]); self.tree.scrollToItem(self.tree_items[item.node.uid]); self.inspect(item)

    def export_png(self):
        if not self.scene:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export current template", self.scene.document.path.stem + ".png", "PNG (*.png)")
        if path:
            try:
                self.scene.export(path)
                self.statusBar().showMessage(f"Exported {path}")
            except (ValueError, OSError) as error:
                QMessageBox.warning(self, "Export failed", str(error))

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if urls:
            path = Path(urls[0].toLocalFile()); self.open_path(path)

    def closeEvent(self, event):
        if not self.confirm_unsaved():
            event.ignore(); return
        if self.session:
            self.session.changed.disconnect(self.refresh_preview)
            self.session.undo.indexChanged.disconnect(self.update_edit_state)
            self.session.undo.cleanChanged.disconnect(self.update_edit_state)
            self.session = None
        if self.scene is not None and not sip.isdeleted(self.scene):
            self.scene.selectionChanged.disconnect(self.select_canvas)
        self.view.setScene(None)
        self.scene = None
        self.tree.blockSignals(True)
        super().closeEvent(event)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", help="JSON/CSD document or template directory; default: writable copy of bundled demo")
    parser.add_argument("--version", action="version", version=f"Cocos UI Preview {__version__}")
    parser.add_argument("--resource-root", type=Path)
    parser.add_argument("--export", type=Path, help="Export one template PNG and exit (no GUI)")
    parser.add_argument("--report", type=Path, help="Write resource/unsupported-feature report")
    parser.add_argument("--show-hidden", action="store_true")
    parser.add_argument("--smoke-test", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--editor-smoke-test", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args([arg for arg in sys.argv[1:] if not arg.startswith("-psn_")])
    app = PreviewApplication([sys.argv[0]]); app.setApplicationName("Cocos UI Preview"); app.setStyle("Fusion")
    app.setOrganizationName("Cocos UI Preview"); app.setApplicationVersion(__version__)
    if args.export:
        try:
            scene = PreviewScene(Document.load(args.path or sample_path()), args.resource_root)
            scene.show_hidden = args.show_hidden; scene.update_visibility()
            args.export.parent.mkdir(parents=True, exist_ok=True); scene.export(args.export)
            report = scene.report()
            if args.report:
                args.report.parent.mkdir(parents=True, exist_ok=True); args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
            print(json.dumps({k: v for k, v in report.items() if k != "resources"}, ensure_ascii=False))
            return 0
        except (ValueError, OSError, TypeError, KeyError, OverflowError, RecursionError) as error:
            print(f"Preview failed: {error}", file=sys.stderr); return 1
    app.setStyleSheet("QWidget { background: #222b36; color: #d6dce6; } QWidget:disabled { color: #718091; } QTreeWidget, QPlainTextEdit, QListWidget { background: #1c2530; } "
                     "QPushButton { padding: 6px 9px; } QComboBox { padding: 5px; } QSplitter::handle { background: #111a25; }")
    initial = args.path
    if args.editor_smoke_test:
        initial = working_demo(args.editor_smoke_test.resolve())
    elif not initial:
        initial = working_demo()
    window = PreviewWindow(initial, args.resource_root); window.hidden.setChecked(args.show_hidden); window.show()
    app.window = window
    if app.pending_file:
        window.open_path(app.pending_file)
    smoke_directory = args.editor_smoke_test or args.smoke_test
    if smoke_directory:
        def smoke():
            try:
                smoke_directory.mkdir(parents=True, exist_ok=True)
                window.view.fit()
                items = list(window.scene.node_items.values())
                selected = next((i for i in items if i.node.kind == "Button"), items[0])
                window.tree.setCurrentItem(window.tree_items[selected.node.uid])
                assert selected.isSelected() and selected.node.name in window.properties.toPlainText()
                selected_name = selected.node.name
                if args.editor_smoke_test:
                    uid = selected.node.uid
                    window.scene.export(smoke_directory / "before.png")
                    control = window.inspector.controls["x"]
                    old_x = control.value(); control.setValue(old_x + 24)
                    assert window.scene.node_items[uid].o["x"] == old_x + 24
                    window.session.undo.undo(); assert window.scene.node_items[uid].o["x"] == old_x
                    window.session.undo.redo(); assert window.scene.node_items[uid].o["x"] == old_x + 24
                    window.inspector.controls["text"].setPlainText("EDITED LIVE")
                    assert window.scene.node_items[uid].text == "EDITED LIVE"
                    destination = window.session.document.path.with_name("demo-edited.json")
                    assert window.save_file(destination=destination)
                    assert not window.session.dirty
                    assert window.load_file(destination)
                    window.tree.setCurrentItem(window.tree_items[uid])
                    assert window.scene.node_items[uid].text == "EDITED LIVE"
                    assert window.scene.node_items[uid].o["x"] == old_x + 24
                window.scene.export(smoke_directory / "canvas.png")
                assert window.grab().save(str(smoke_directory / "window.png"))
                report = {"version": __version__, "frozen": bool(getattr(sys, "frozen", False)),
                          "window_visible": window.isVisible(), "selected_node": selected_name,
                          "editor_edit_undo_redo_save_reopen": bool(args.editor_smoke_test),
                          "scene": window.scene.report()}
                (smoke_directory / "smoke.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            except Exception:
                import traceback
                traceback.print_exc(); app.exit(1); return
            window.close(); app.exit(0)
        QTimer.singleShot(500, smoke)
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
