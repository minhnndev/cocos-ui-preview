"""Offline adapter regressions; synthetic pixels are not native game evidence."""
import json
import os
from pathlib import Path
import plistlib
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PIL import Image
from PyQt5.QtCore import QPointF, QRectF
from PyQt5.QtGui import QImage, QPainter
from PyQt5.QtWidgets import QApplication

from cocos_ui_preview.model import Document, Resources, tinted
from cocos_ui_preview.scene import PreviewScene, draw_scale9, qimage
from cocos_ui_preview.app import PreviewWindow

GAME_ROOT = os.environ.get("COCOS_UI_PREVIEW_GAME_ROOT")
ROOT = Path(GAME_ROOT).expanduser().resolve() if GAME_ROOT else None
HAS_GAME_FIXTURES = bool(ROOT and all((ROOT / "res/studio_ui" / name).is_file()
                                    for name in ["NewPlayerLayer.json", "ToolKitLayer.json"]))

APP = QApplication.instance() or QApplication([])


def widget(kind="Panel", children=(), **options):
    return {"classname": kind, "options": {"width": 100, "height": 100,
            "anchorPointX": 0, "anchorPointY": 0, **options}, "children": list(children)}


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def document(self, root=None, **extra):
        path = self.root / "screen.json"
        path.write_text(json.dumps({"designWidth": 100, "designHeight": 100,
                                   "widgetTree": root or widget(), **extra}))
        return Document.load(path)

    def render(self, scene):
        path = self.root / "output.png"
        scene.export(path)
        return Image.open(path).convert("RGBA")

    def test_schema_errors_and_duplicate_names(self):
        doc = self.document(widget(children=[widget(name="same"), widget(name="same")]))
        self.assertEqual([n.uid for n in doc.root.walk()], ["0", "0/0", "0/1"])
        self.assertEqual(len(PreviewScene(doc).node_items), 3)
        for content in [[], {}, {"widgetTree": {"options": None}}]:
            doc.path.write_text(json.dumps(content))
            with self.assertRaises(ValueError):
                Document.load(doc.path)
        with self.assertRaisesRegex(ValueError, "CSB"):
            Document.load(self.root / "test.csb")

    def test_non_layout_parent_anchor_matches_guireader(self):
        child = widget("ImageView", width=4, height=4, x=0, y=2,
                       anchorPointX=.5, anchorPointY=.5)
        button = widget("Button", width=20, height=10, x=40, y=30,
                        anchorPointX=.5, anchorPointY=.5, scaleX=2, scaleY=3, children=[child])
        scene = PreviewScene(self.document(widget(children=[button])))
        point = scene.node_items["0/0/0"].mapToScene(QPointF(2, 2))
        self.assertEqual((point.x(), point.y()), (40, 64))
        # Layout parents use authored child coordinates without anchor compensation.
        button["classname"] = "Panel"
        scene = PreviewScene(self.document(widget(children=[button])))
        point = scene.node_items["0/0/0"].mapToScene(QPointF(2, 2))
        self.assertEqual((point.x(), point.y()), (20, 79))

    def test_percent_size_position_and_rotation(self):
        child = widget("ImageView", sizeType=1, sizePercentX=.2, sizePercentY=.1,
                       positionType=1, positionPercentX=.25, positionPercentY=.5,
                       rotation=90, scaleX=2, scaleY=3)
        item = PreviewScene(self.document(widget(children=[child]))).node_items["0/0"]
        self.assertEqual((item.width, item.height), (20, 10))
        point = item.mapToScene(QPointF(20, 10))
        self.assertAlmostEqual(point.x(), 25)
        self.assertAlmostEqual(point.y(), 90)

    def test_exact_resource_paths_intrinsic_size_and_tint(self):
        Image.new("RGBA", (7, 9), (200, 100, 60, 128)).save(self.root / "icon.png")
        doc = self.document(widget(children=[widget("ImageView", fileNameData={"path": "icon.png"})]))
        scene = PreviewScene(doc)
        item = scene.node_items["0/0"]
        self.assertEqual((item.width, item.height), (7, 9))
        self.assertEqual(tinted(Image.new("RGBA", (1, 1), (200, 100, 60, 128)),
                                {"colorR": 128}).getpixel((0, 0)), (100, 100, 60, 128))
        resources = Resources(doc)
        self.assertIsNone(resources.image({"path": "wrong/icon.png"}, "owner"))
        self.assertTrue(any("Missing texture" in x for x in resources.issues))

    def test_atlas_rotation_trim_alias_and_ambiguity(self):
        logical = Image.new("RGBA", (2, 3), "red")
        logical.putpixel((1, 2), (0, 255, 0, 255))
        logical.transpose(Image.Transpose.ROTATE_270).save(self.root / "atlas.png")
        frame2 = {"frame": "{{0,0},{2,3}}", "rotated": True,
                  "sourceSize": "{6,7}", "offset": "{1,-1}"}
        frame3 = {"textureRect": "{{0,0},{3,2}}", "spriteSize": "{2,3}",
                  "textureRotated": True, "spriteSourceSize": "{6,7}",
                  "spriteOffset": "{1,-1}", "aliases": ["alias"]}
        for fmt, frame in [(2, frame2), (3, frame3)]:
            (self.root / f"atlas{fmt}.plist").write_bytes(plistlib.dumps({
                "metadata": {"format": fmt, "textureFileName": "atlas.png"}, "frames": {"icon": frame}}))
        doc = self.document(textures=["atlas2.plist"])
        resources = Resources(doc)
        for name, plist in [("icon", "atlas2.plist"), ("alias", "atlas3.plist")]:
            image = resources.image({"path": name, "resourceType": 1, "plistFile": plist})
            self.assertEqual(image.size, (6, 7))
            self.assertEqual(image.getpixel((4, 5)), (0, 255, 0, 255))
            self.assertEqual(image.getpixel((0, 0))[3], 0)
        doc.data["textures"] = ["atlas2.plist", "atlas3.plist"]
        self.assertIsNone(resources.image({"path": "icon", "resourceType": 1}))
        self.assertTrue(any("ambiguous" in x for x in resources.issues))
        self.assertIsNone(resources.image({"path": "absent", "resourceType": 1}))

    def test_bitmap_font_pages_kerning_and_missing_glyph(self):
        Image.new("RGBA", (2, 2), "red").save(self.root / "glyphs.png")
        (self.root / "font.fnt").write_text('common lineHeight=3\npage id=0 file="glyphs.png"\n'
            'char id=65 x=0 y=0 width=2 height=2 xoffset=0 yoffset=1 xadvance=3 page=0\n'
            'kerning first=65 second=65 amount=-1\n')
        resources = Resources(self.document())
        image = resources.bitmap_text({"path": "font.fnt"}, "AA\nA", "label")
        self.assertEqual(image.size, (5, 6))
        self.assertEqual(image.getpixel((2, 1)), (255, 0, 0, 255))
        self.assertEqual(image.getpixel((0, 4)), (255, 0, 0, 255))
        resources.bitmap_text({"path": "font.fnt"}, "Z", "label")
        self.assertTrue(any("no glyph" in x for x in resources.issues))

    def test_scale9_preserves_corner_pixels(self):
        image = Image.new("RGBA", (3, 3), "green")
        image.putpixel((0, 0), (255, 0, 0, 255))
        image.putpixel((2, 2), (0, 0, 255, 255))
        target = QImage(9, 7, QImage.Format_ARGB32); target.fill(0)
        painter = QPainter(target)
        draw_scale9(painter, qimage(image), QRectF(0, 0, 9, 7), {})
        painter.end()
        self.assertEqual(target.pixelColor(0, 0).name(), "#ff0000")
        self.assertEqual(target.pixelColor(8, 6).name(), "#0000ff")
        self.assertEqual(target.pixelColor(4, 3).name(), "#008000")

    def test_loading_bar_direction_and_flip(self):
        Image.new("RGBA", (10, 4), "red").save(self.root / "bar.png")
        child = widget("LoadingBar", x=20, y=20, percent=50, direction=1,
                       textureData={"path": "bar.png"})
        image = self.render(PreviewScene(self.document(widget(children=[child]))))
        self.assertEqual(image.getpixel((28, 78))[:3], (255, 0, 0))
        self.assertNotEqual(image.getpixel((21, 78))[:3], (255, 0, 0))
        child["options"]["flipX"] = True
        image = self.render(PreviewScene(self.document(widget(children=[child]))))
        self.assertEqual(image.getpixel((21, 78))[:3], (255, 0, 0))
        self.assertNotEqual(image.getpixel((28, 78))[:3], (255, 0, 0))

    def test_inherited_visibility_clipping_and_export_bounds(self):
        red = widget("Panel", width=20, height=20, x=15, y=0,
                     colorType=1, bgColorR=255, bgColorG=0, bgColorB=0)
        parent = widget("Panel", width=20, height=20, x=10, y=10,
                        clipAble=True, visible=False, children=[red])
        scene = PreviewScene(self.document(widget(children=[parent])))
        self.assertFalse(scene.node_items["0/0/0"].isVisible())
        scene.show_hidden = True; scene.update_visibility(); scene.bounds = True
        scene.node_items["0/0/0"].setSelected(True)
        image = self.render(scene)
        self.assertEqual(image.getpixel((27, 75))[:3], (255, 0, 0))
        self.assertNotEqual(image.getpixel((32, 75))[:3], (255, 0, 0))
        self.assertNotEqual(image.getpixel((25, 70))[:3], (54, 212, 232))

    @unittest.skipUnless(HAS_GAME_FIXTURES, "Set COCOS_UI_PREVIEW_GAME_ROOT to a compatible local game checkout")
    def test_real_templates_and_gui_selection_reload(self):
        window = PreviewWindow(ROOT / "res/studio_ui/NewPlayerLayer.json")
        self.addCleanup(window.close)
        self.assertEqual(len(window.scene.node_items), 26)
        button = next(x for x in window.scene.node_items.values() if x.node.name == "btn_start")
        window.tree.setCurrentItem(window.tree_items[button.node.uid])
        self.assertTrue(button.isSelected())
        self.assertIn('"name": "btn_start"', window.properties.toPlainText())
        label = next(x for x in button.childItems() if x.node.kind == "Label")
        self.assertAlmostEqual(label.sceneBoundingRect().center().x(), button.sceneBoundingRect().center().x())
        window.isolate.setChecked(True)
        self.assertEqual(window.scene.isolated, button.node.uid)
        window.reload(); APP.processEvents()
        self.assertIsNone(window.scene.isolated)
        self.assertTrue(window.load_file(ROOT / "res/studio_ui/ToolKitLayer.json"))
        self.assertEqual(len(window.scene.node_items), 525)
        self.assertFalse(any("Missing texture" in x for x in window.scene.resources.issues))

    @unittest.skipUnless(HAS_GAME_FIXTURES, "Set COCOS_UI_PREVIEW_GAME_ROOT to a compatible local game checkout")
    def test_editor_roundtrip_on_copies_of_game_templates(self):
        from copy import deepcopy
        from cocos_ui_preview.editor import raw_node
        for filename, name, count in [("NewPlayerLayer.json", "btn_start", 26),
                                      ("ToolKitLayer.json", "panel_task_info", 525)]:
            source = ROOT / "res/studio_ui" / filename
            original = source.read_bytes()
            copy = self.root / filename; copy.write_bytes(original)
            window = PreviewWindow(copy, ROOT / "res")
            try:
                item = next(i for i in window.scene.node_items.values() if i.node.name == name)
                uid = item.node.uid
                window.tree.setCurrentItem(window.tree_items[uid])
                before = deepcopy(window.session.document.data)
                old_x = float(item.o.get("x", 0))
                window.inspector.controls["x"].setValue(old_x + 12)
                window.session.undo.undo()
                self.assertEqual(window.session.document.data, before)
                window.session.undo.redo()
                destination = copy.with_name(copy.stem + "-edited.json")
                self.assertTrue(window.save_file(destination=destination))
                expected = deepcopy(before); raw_node(expected, uid)["options"]["x"] = old_x + 12
                self.assertEqual(Document.load(destination).data, expected)
                self.assertTrue(window.load_file(destination))
                self.assertEqual(len(window.scene.node_items), count)
                self.assertEqual(window.scene.node_items[uid].o["x"], old_x + 12)
                self.assertEqual(source.read_bytes(), original)
            finally:
                window.session.undo.setClean(); window.close(); APP.processEvents()


if __name__ == "__main__":
    unittest.main()
