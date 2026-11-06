import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5.QtCore import QPointF, QEvent
from PyQt5.QtWidgets import QApplication

from cocos_ui_preview.app import PreviewWindow, sample_path
from cocos_ui_preview.model import Document, Resources
from cocos_ui_preview.scene import PreviewScene

APP = QApplication.instance() or QApplication([])


class StudioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_csd_button_resource_and_bottom_left_coordinates(self):
        scene = PreviewScene(Document.load(sample_path().with_name("studio.csd")))
        self.assertEqual(scene.document.format, "csd")
        button = scene.node_items["0/0"]
        self.assertEqual((button.width, button.height), (240, 64))
        self.assertEqual(button.image.width(), 80)
        center = button.sceneBoundingRect().center()
        self.assertEqual((center.x(), center.y()), (320, 180))
        self.assertFalse(any("Missing" in x for x in scene.resources.issues))

    def test_studio_json_does_not_apply_legacy_parent_anchor_compensation(self):
        label = {"ctype": "TextObjectData", "Name": "label", "LabelText": "Hi",
                 "Size": {"X": 20, "Y": 10}, "Position": {"X": 0, "Y": 0},
                 "AnchorPoint": {"ScaleX": .5, "ScaleY": .5}}
        button = {"ctype": "ButtonObjectData", "Name": "button", "Size": {"X": 100, "Y": 40},
                  "AnchorPoint": {"ScaleX": .5, "ScaleY": .5}, "Position": {"X": 100, "Y": 50},
                  "Children": [label]}
        data = {"Content": {"Content": {"ObjectData": {"ctype": "GameLayerObjectData",
                "Size": {"X": 200, "Y": 100}, "Children": [button]}}}}
        path = self.root / "modern.json"; path.write_text(json.dumps(data))
        scene = PreviewScene(Document.load(path))
        center = scene.node_items["0/0/0"].sceneBoundingRect().center()
        self.assertEqual((center.x(), center.y()), (50, 70))

    def test_invalid_xml_entity_dimensions_and_nonfinite(self):
        path = self.root / "broken.csd"
        for xml in ["<broken>", "<GameFile/>", '<!DOCTYPE x [<!ENTITY t "bad">]><GameFile/>']:
            path.write_text(xml)
            with self.assertRaises(ValueError):
                Document.load(path)
        data = json.loads(sample_path().read_text())
        path = self.root / "broken.json"
        for width in ["nan", -1, 100000]:
            data["designWidth"] = width; path.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                Document.load(path)
        data["designWidth"] = 960
        data["widgetTree"]["options"]["x"] = "inf"; path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "Non-finite"):
            Document.load(path)

    def test_resource_root_is_portable_without_res_directory(self):
        data = json.loads(sample_path().read_text()); path = self.root / "external.json"
        path.write_text(json.dumps(data))
        resources = Resources(Document.load(path), sample_path().parent)
        image = resources.image({"path": "panel.png"})
        self.assertIsNotNone(image)
        self.assertIsNone(resources.image({"path": "elsewhere/panel.png"}))

    def test_gui_selection_canvas_sync_isolate_reload_and_failed_load(self):
        window = PreviewWindow(sample_path()); self.addCleanup(window.close)
        button = next(i for i in window.scene.node_items.values() if i.node.kind == "Button")
        button.setSelected(True)
        self.assertEqual(window.tree.currentItem().data(0, 256), button.node.uid)
        self.assertIn('"name": "preview_button"', window.properties.toPlainText())
        window.isolate.setChecked(True)
        self.assertEqual(window.scene.isolated, button.node.uid)
        window.hidden.setChecked(True)
        self.assertTrue(all(i.isVisible() for i in window.scene.node_items.values()))
        window.reload(); APP.processEvents()
        self.assertIsNone(window.scene.isolated)
        previous = window.scene
        with patch("cocos_ui_preview.app.QMessageBox.warning") as warning:
            self.assertFalse(window.load_file(self.root / "missing.csd"))
            warning.assert_called_once()
        self.assertIs(window.scene, previous)
        self.assertTrue(window.open_path(sample_path().with_name("studio.csd")))
        self.assertEqual(window.scene.document.format, "csd")

    def test_cli_can_export_outside_repository_and_invalid_file_exits_nonzero(self):
        env = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
        command = [sys.executable, "-m", "cocos_ui_preview"]
        for filename in [sample_path(), sample_path().with_name("studio.csd")]:
            export = self.root / "preview.png"; report = self.root / "preview.json"
            result = subprocess.run(command + [str(filename), "--export", str(export), "--report", str(report)],
                                    cwd=self.root, env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(export.exists())
            self.assertGreater(json.loads(report.read_text())["nodes"], 1)
        result = subprocess.run(command + ["unsupported.csb", "--export", str(self.root / "bad.png")],
                                cwd=self.root, env=env, capture_output=True, text=True, timeout=30)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CSB", result.stderr)
        self.assertFalse((self.root / "bad.png").exists())

    def test_studio_constraints_and_unsupported_nodes_reported(self):
        data = {"ObjectData": {"ctype": "GameLayerObjectData", "Size": {"X": 200, "Y": 100},
                "Children": [{"ctype": "ProjectNodeObjectData", "Name": "nested",
                              "PrePosition": {"X": .5}, "RotationSkewX": 10, "RotationSkewY": 20}]}}
        path = self.root / "modern.json"; path.write_text(json.dumps(data))
        issues = PreviewScene(Document.load(path)).report()["issues"]
        for expected in ["not expanded", "constraints", "RotationSkewX/Y", "Unsupported widget"]:
            self.assertTrue(any(expected in issue for issue in issues), issues)

    def test_app_shutdown_with_selected_node_exits_cleanly(self):
        result = subprocess.run([sys.executable, "-m", "cocos_ui_preview", "--smoke-test", str(self.root / "smoke")],
                                env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
                                cwd=self.root, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        report = json.loads((self.root / "smoke/smoke.json").read_text())
        self.assertTrue(report["window_visible"])
        self.assertEqual(report["selected_node"], "preview_button")


if __name__ == "__main__":
    unittest.main()
