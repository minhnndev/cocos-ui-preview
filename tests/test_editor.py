import codecs
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5.QtWidgets import QApplication, QMessageBox
from PyQt5 import sip

from cocos_ui_preview.app import PreviewWindow, sample_path, working_demo
from cocos_ui_preview.editor import EditorSession, raw_node
from cocos_ui_preview.model import Document
from cocos_ui_preview.scene import PreviewScene

APP = QApplication.instance() or QApplication([])


class EditorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = working_demo(self.root)
        self.original = self.path.read_bytes()
        self.session = EditorSession(Document.load(self.path))
        self.uid = "0/2/2"

    def options(self):
        return raw_node(self.session.document.data, self.uid)["options"]

    def window(self, path=None):
        window = PreviewWindow(path or self.path)
        def close():
            if window.session:
                window.session.undo.setClean()
            window.close(); APP.processEvents()
        self.addCleanup(close)
        window.tree.setCurrentItem(window.tree_items[self.uid if path is None else "0/0"])
        return window

    def test_numeric_commands_merge_undo_redo_and_restore_absent_keys(self):
        self.session.edit(self.uid, "rotation", 15)
        self.session.edit(self.uid, "rotation", 30)
        self.assertEqual(self.options()["rotation"], 30)
        self.assertEqual(self.session.undo.count(), 1)
        self.assertTrue(self.session.dirty)
        self.session.undo.undo()
        self.assertNotIn("rotation", self.options())
        self.assertFalse(self.session.dirty)
        self.session.undo.redo(); self.assertEqual(self.options()["rotation"], 30)
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_negative_scale_and_outside_anchor_valid_but_invalid_fields_rejected(self):
        self.session.edit(self.uid, "scaleX", -2)
        self.session.edit(self.uid, "anchorPointX", 1.5)
        count = self.session.undo.count()
        for key, value in [("width", -1), ("opacity", 256), ("rotation", float("nan")),
                           ("visible", 1), ("opacity", 1.5), ("positionType", 3),
                           ("color", (255, -1, 0)), ("text", 42), ("percent", 55)]:
            with self.assertRaises(ValueError):
                self.session.edit(self.uid, key, value)
        self.assertEqual(self.session.undo.count(), count)

    def test_nested_resources_and_colors_reset_preserve_unknown_members(self):
        self.options()["normalData"]["custom"] = {"unknown": [1, 2]}
        self.session.edit(self.uid, "normalData.path", "tile.png")
        self.session.edit(self.uid, "textColor", (10, 20, 30))
        self.assertEqual(self.options()["normalData"]["custom"], {"unknown": [1, 2]})
        self.session.edit(self.uid, "normalData.path", reset=True)
        self.assertEqual(self.options()["normalData"]["path"], "button.png")
        self.assertEqual(self.options()["normalData"]["custom"], {"unknown": [1, 2]})
        self.session.edit(self.uid, "textColor", reset=True)
        self.assertEqual(self.options()["textColorR"], 17)
        self.session.undo.undo(); self.assertEqual(self.options()["textColorR"], 10)
        self.session.edit(self.uid, "normalData.resourceType", 1)
        self.session.edit(self.uid, "normalData.resourceType", reset=True)
        self.assertNotIn("resourceType", self.options()["normalData"])

    def test_roundtrip_preserves_unknown_data_bom_newlines_and_only_target_property(self):
        data = json.loads(self.original)
        data["animation"] = {"frames": [1, {"future": "untouched"}]}
        data["widgetTree"]["options"]["unknown"] = {"nested": True}
        original_data = deepcopy(data)
        payload = codecs.BOM_UTF8 + (json.dumps(data, indent="\t").replace("\n", "\r\n") + "\r\n").encode()
        self.path.write_bytes(payload)
        self.session = EditorSession(Document.load(self.path))
        self.session.edit(self.uid, "text", "Xin chào 🌱")
        self.session.save()
        expected = deepcopy(original_data); raw_node(expected, self.uid)["options"]["text"] = "Xin chào 🌱"
        self.assertEqual(Document.load(self.path).data, expected)
        saved = self.path.read_bytes()
        self.assertTrue(saved.startswith(codecs.BOM_UTF8))
        self.assertIn(b'\r\n\t"', saved)
        self.assertFalse(self.session.dirty)
        self.session.undo.undo(); self.assertTrue(self.session.dirty)
        self.session.undo.redo(); self.assertFalse(self.session.dirty)

    def test_save_as_reopen_resource_and_source_unchanged(self):
        self.session.edit(self.uid, "normalData.path", "tile.png")
        destination = self.path.with_name("copy.json")
        self.session.save(destination)
        self.assertEqual(self.path.read_bytes(), self.original)
        scene = PreviewScene(Document.load(destination))
        self.assertIsNotNone(scene.node_items[self.uid].image)
        self.assertEqual(self.session.document.path, destination.resolve())
        self.session.edit(self.uid, "text", "After Save As")
        self.session.save()
        self.assertEqual(raw_node(Document.load(destination).data, self.uid)["options"]["text"], "After Save As")

    def test_disk_conflict_failed_write_and_unsafe_save_as_keep_dirty(self):
        self.session.edit(self.uid, "x", 444)
        self.path.write_bytes(self.original + b" ")
        with self.assertRaisesRegex(ValueError, "changed on disk"):
            self.session.save()
        self.assertEqual(self.path.read_bytes(), self.original + b" ")
        with self.assertRaisesRegex(ValueError, "source folder"):
            self.session.save(self.root / "elsewhere.json")
        existing = self.path.with_name("existing.json"); existing.write_bytes(b"keep me")
        with self.assertRaisesRegex(ValueError, "new filename"):
            self.session.save(existing)
        self.assertEqual(existing.read_bytes(), b"keep me")
        self.assertTrue(self.session.dirty)

    def test_save_failures_leave_source_and_history_untouched(self):
        self.session.edit(self.uid, "x", 444)
        with patch("cocos_ui_preview.editor.QSaveFile") as file:
            file.return_value.open.return_value = False
            file.return_value.errorString.return_value = "Read-only folder"
            with self.assertRaises(OSError):
                self.session.save()
        self.assertTrue(self.session.dirty)
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertFalse(any(self.path.parent.glob("*.tmp")))
        for stage in ["write", "commit"]:
            with patch("cocos_ui_preview.editor.QSaveFile") as file:
                file.return_value.open.return_value = True
                file.return_value.write.side_effect = lambda data: len(data) if stage == "commit" else -1
                file.return_value.commit.return_value = False
                file.return_value.errorString.return_value = "Disk full"
                with self.assertRaises(OSError):
                    self.session.save()
                if stage == "write":
                    file.return_value.cancelWriting.assert_called_once()
            self.assertTrue(self.session.dirty)
            self.assertEqual(self.path.read_bytes(), self.original)

    def test_studio_and_csd_writes_rejected(self):
        modern = self.root / "modern.json"
        modern.write_text(json.dumps({"ObjectData": {"ctype": "GameLayerObjectData", "Size": {"X": 100, "Y": 100}}}))
        for path in [sample_path().with_name("studio.csd"), modern]:
            session = EditorSession(Document.load(path))
            self.assertFalse(session.editable)
            with self.assertRaises(ValueError):
                session.edit("0/0", "x", 12)
            with self.assertRaises(ValueError):
                session.save(self.path)

    def test_duplicate_keys_and_nonstandard_numbers_are_view_only(self):
        for extra in ['"future": 1, "future": 2,', '"future": NaN,']:
            self.path.write_text("{" + extra + self.original.decode()[1:])
            session = EditorSession(Document.load(self.path))
            self.assertFalse(session.editable)
            with self.assertRaises(ValueError):
                session.save()

    def test_live_controls_geometry_color_text_preview_and_selection_survive_undo(self):
        window = self.window(); window.show(); APP.processEvents()
        window.view.scale(1.3, 1.3); transform = window.view.transform()
        window.isolate.setChecked(True)
        expanded = window.tree_items["0/2"].isExpanded()
        before = window.scene.node_items[self.uid].sceneBoundingRect()
        control = window.inspector.controls["x"]; old_x = control.value(); control.setValue(old_x + 10)
        item = window.scene.node_items[self.uid]
        self.assertAlmostEqual(item.sceneBoundingRect().x() - before.x(), 10)
        self.assertTrue(item.isSelected()); self.assertEqual(window.scene.isolated, self.uid)
        self.assertEqual(window.view.transform(), transform)
        self.assertEqual(window.tree_items["0/2"].isExpanded(), expanded)
        self.assertIs(window.inspector.controls["x"], control)
        window.inspector.controls["opacity"].setValue(100)
        self.assertAlmostEqual(window.scene.node_items[self.uid].opacity(), 100 / 255)
        window.inspector.sliders["opacity"].setValue(125)
        self.assertEqual(window.scene.node_items[self.uid].o["opacity"], 125)
        self.assertTrue(window.edit_property("textColor", (3, 4, 5)))
        self.assertTrue(window.edit_property("text", "CHANGED"))
        self.assertEqual(window.scene.node_items[self.uid].text, "CHANGED")
        window.session.undo.undo(); self.assertEqual(window.scene.node_items[self.uid].text, "PREVIEW")
        self.assertTrue(window.save_button.isEnabled())
        self.assertIn("*", window.windowTitle())

    def test_inactive_size_modes_readonly_format_and_noop_string_focus(self):
        window = self.window()
        self.assertFalse(window.inspector.controls["width"].isEnabled())
        self.assertTrue(window.inspector.controls["scale9Width"].isEnabled())
        name = window.inspector.controls["name"]; name.editingFinished.emit()
        self.assertFalse(window.session.dirty)
        window.edit_property("scale9Enable", False)
        window.edit_property("ignoreSize", False)
        self.assertTrue(window.inspector.controls["width"].isEnabled())
        window.session.undo.setClean()
        window.load_file(sample_path().with_name("studio.csd"))
        window.tree.setCurrentItem(window.tree_items["0/0"])
        self.assertFalse(window.inspector.controls["x"].isEnabled())
        self.assertFalse(window.save_as_button.isEnabled())

    def test_unsaved_cancel_discard_save_failure_and_success(self):
        window = self.window(); window.edit_property("text", "UNSAVED")
        scene = window.scene
        with patch("cocos_ui_preview.app.QMessageBox.warning", return_value=QMessageBox.Cancel):
            self.assertFalse(window.load_file(sample_path().with_name("studio.csd")))
            window.close(); self.assertIs(window.scene, scene)
        with patch("cocos_ui_preview.app.QMessageBox.warning", return_value=QMessageBox.Save):
            with patch.object(window, "save_file", return_value=False):
                self.assertFalse(window.confirm_unsaved())
            self.assertTrue(window.confirm_unsaved())
        self.assertFalse(window.session.dirty)
        self.assertEqual(raw_node(Document.load(self.path).data, self.uid)["options"]["text"], "UNSAVED")
        window.edit_property("text", "discard me")
        with patch("cocos_ui_preview.app.QMessageBox.warning", return_value=QMessageBox.Discard):
            self.assertTrue(window.load_file(sample_path().with_name("studio.csd")))

    def test_resource_root_refresh_keeps_unsaved_values(self):
        window = self.window(); window.edit_property("text", "KEEP ME")
        with patch("cocos_ui_preview.app.QFileDialog.getExistingDirectory", return_value=str(self.path.parent)):
            window.choose_resource_root()
        self.assertTrue(window.session.dirty)
        self.assertEqual(window.scene.node_items[self.uid].text, "KEEP ME")

    def test_live_multiline_typing_keeps_cursor_and_is_saved_on_document_switch(self):
        window = self.window()
        text = window.inspector.controls["text"]
        text.moveCursor(text.textCursor().End)
        text.insertPlainText(" TEST")
        self.assertEqual(window.scene.node_items[self.uid].text, "PREVIEW TEST")
        self.assertEqual(text.textCursor().position(), len("PREVIEW TEST"))
        self.assertTrue(window.session.dirty)
        with patch("cocos_ui_preview.app.QMessageBox.warning", return_value=QMessageBox.Save):
            window.load_file(sample_path().with_name("studio.csd"))
        self.assertEqual(raw_node(Document.load(self.path).data, self.uid)["options"]["text"], "PREVIEW TEST")

    def test_built_in_demo_is_copied_once_and_never_overwrites_user_edits(self):
        self.path.write_text("user data")
        self.assertEqual(working_demo(self.root), self.path)
        self.assertEqual(self.path.read_text(), "user data")
        self.assertNotEqual(self.path.parent, sample_path().parent)

    def test_undo_stack_destruction_does_not_call_deleted_qt_objects(self):
        window = self.window()
        sip.delete(window.session.undo)
        window.update_edit_state()
        self.assertFalse(window.undo_button.isEnabled())
        window.session = None

    def test_subprocess_editor_smoke_edits_saves_reopens_and_exits_cleanly(self):
        result = subprocess.run([sys.executable, "-m", "cocos_ui_preview", "--editor-smoke-test", str(self.root / "smoke")],
                                env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        report = json.loads((self.root / "smoke/smoke.json").read_text())
        self.assertTrue(report["editor_edit_undo_redo_save_reopen"])
        self.assertEqual(report["version"], "0.2.0")


if __name__ == "__main__":
    unittest.main()
