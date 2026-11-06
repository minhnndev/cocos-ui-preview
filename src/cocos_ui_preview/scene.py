"""Qt template renderer. No Lua, input mutation or animation simulation."""
from __future__ import annotations

from PyQt5.QtCore import QPointF, QRectF, Qt
from PyQt5.QtGui import QColor, QFont, QFontDatabase, QFontMetricsF, QImage, QLinearGradient, QPainter, QPen, QTransform
from PyQt5.QtWidgets import QGraphicsItem, QGraphicsScene, QStyle

from .model import Resources, tinted

FONT_FAMILIES = {}

def qimage(image):
    rgba = image.convert("RGBA")
    return QImage(rgba.tobytes(), rgba.width, rgba.height, rgba.width * 4, QImage.Format_RGBA8888).copy()


def color(options, prefix="color", alpha=255):
    return QColor(*(int(options.get(prefix + c, 255)) for c in "RGB"), int(alpha))


def draw_scale9(painter, image, target, options):
    """Cocos capInsets are measured from the texture's top-left."""
    width, height = image.width(), image.height()
    x, y = float(options.get("capInsetsX", 0)), float(options.get("capInsetsY", 0))
    w, h = float(options.get("capInsetsWidth", 0)), float(options.get("capInsetsHeight", 0))
    if (x, y, w, h) == (0, 0, 0, 0):
        x, y, w, h = width / 3, height / 3, width / 3, height / 3
    x, y = max(0, min(width, x)), max(0, min(height, y))
    w, h = max(0, min(width - x, w)), max(0, min(height - y, h))
    left, right, top, bottom = x, width - x - w, y, height - y - h
    sx = min(1, target.width() / (left + right)) if left + right else 1
    sy = min(1, target.height() / (top + bottom)) if top + bottom else 1
    source_x, source_y = [0, x, x + w, width], [0, y, y + h, height]
    target_x = [target.left(), target.left() + left * sx, target.right() - right * sx, target.right()]
    target_y = [target.top(), target.top() + top * sy, target.bottom() - bottom * sy, target.bottom()]
    for row in range(3):
        for col in range(3):
            src = QRectF(source_x[col], source_y[row], source_x[col + 1] - source_x[col], source_y[row + 1] - source_y[row])
            dst = QRectF(target_x[col], target_y[row], target_x[col + 1] - target_x[col], target_y[row + 1] - target_y[row])
            if src.width() > 0 and src.height() > 0 and dst.width() > 0 and dst.height() > 0:
                painter.drawImage(dst, image, src)


class NodeItem(QGraphicsItem):
    def __init__(self, node, preview, parent=None):
        super().__init__(parent)
        self.node, self.preview = node, preview
        self.o = node.options
        self.setFlag(self.ItemIsSelectable)
        self.setFlag(self.ItemNegativeZStacksBehindParent)
        self.setZValue(float(self.o.get("ZOrder", 0)))
        self.setOpacity(max(0, min(1, float(self.o.get("opacity", 255)) / 255)))
        self.setToolTip(f"{node.name} · {node.kind}\n{node.uid}")
        self.image = None
        self.text = str(self.o.get("text") or (self.o.get("placeHolder") if node.kind == "TextField" else "") or "")
        self.font = preview.font_for(self.o)
        self.scale9 = bool(self.o.get("scale9Enable") or self.o.get("backGroundScale9Enable"))
        key = {"Button": "normalData", "ImageView": "fileNameData", "Sprite": "fileNameData", "LoadingBar": "textureData"}.get(node.kind, "backGroundImageData")
        self.reference = self.o.get(key)
        if node.kind == "LabelBMFont":
            bitmap = preview.resources.bitmap_text(self.o.get("fileNameData"), self.text, node.name)
        else:
            bitmap = preview.resources.image(self.reference, node.name)
        if bitmap is not None:
            self.image = qimage(tinted(bitmap, self.o))
        self.width, self.height = float(self.o.get("width", 0)), float(self.o.get("height", 0))
        parent_width, parent_height = (parent.child_width, parent.child_height) if parent else (preview.document.width, preview.document.height)
        if self.o.get("adaptScreen"):
            self.width, self.height = preview.document.width, preview.document.height
        elif self.o.get("sizeType") == 1:
            self.width = parent_width * float(self.o.get("sizePercentX", 0))
            self.height = parent_height * float(self.o.get("sizePercentY", 0))
        if self.scale9:
            self.width = float(self.o.get("scale9Width", self.width))
            self.height = float(self.o.get("scale9Height", self.height))
        elif self.image is not None and self.o.get("ignoreSize", True) and node.kind in {"Button", "ImageView", "LoadingBar", "LabelBMFont"}:
            self.width, self.height = self.image.width(), self.image.height()
        elif node.kind in {"Label", "TextField"} and self.o.get("ignoreSize", True) and self.text:
            metrics = QFontMetricsF(self.font)
            area_width, area_height = float(self.o.get("areaWidth", 0)), float(self.o.get("areaHeight", 0))
            if area_width > 0:
                bounds = metrics.boundingRect(QRectF(0, 0, area_width, 100000), Qt.TextWordWrap, self.text)
                self.width, self.height = area_width, area_height or bounds.height()
            else:
                lines = self.text.split("\n")
                self.width = max(metrics.horizontalAdvance(line) for line in lines)
                self.height = metrics.height() * len(lines)
        self.width, self.height = max(0, self.width), max(0, self.height)
        x, y = float(self.o.get("x", 0)), float(self.o.get("y", 0))
        if parent and self.o.get("positionType") == 1:
            x = parent_width * float(self.o.get("positionPercentX", 0))
            y = parent_height * float(self.o.get("positionPercentY", 0))
        # GUIReader compensates child positions for non-Layout parent anchors.
        # See CCSGUIReader.cpp widgetFromJsonDictionary, before addChild().
        if parent and preview.document.format == "legacy-json" and parent.node.kind not in {"Panel", "ScrollView", "ListView", "PageView"}:
            x += parent.width * float(parent.o.get("anchorPointX", .5))
            y += parent.height * float(parent.o.get("anchorPointY", .5))
        anchor_x, anchor_y = float(self.o.get("anchorPointX", .5)), float(self.o.get("anchorPointY", .5))
        transform = QTransform()
        transform.translate(x, parent_height - y)
        transform.rotate(float(self.o.get("rotation", 0)))
        transform.scale(float(self.o.get("scaleX", 1)), float(self.o.get("scaleY", 1)))
        transform.translate(-anchor_x * self.width, -(1 - anchor_y) * self.height)
        self.setTransform(transform)
        # ScrollView children live in an inner container, initially at its top.
        self.child_height = max(self.height, float(self.o.get("innerHeight", self.height))) if node.kind in {"ScrollView", "ListView"} else self.height
        self.child_width = max(self.width, float(self.o.get("innerWidth", self.width))) if node.kind in {"ScrollView", "ListView"} else self.width
        if self.o.get("clipAble") or node.kind in {"ScrollView", "ListView", "PageView"}:
            self.setFlag(self.ItemClipsChildrenToShape)
        self.missing = bool(self.reference and self.reference.get("path") and self.image is None)

    def boundingRect(self):
        return QRectF(0, 0, max(.01, self.width), max(.01, self.height))

    def paint(self, painter, option, widget=None):
        if self.preview.isolated and not (self.node.uid == self.preview.isolated or self.node.uid.startswith(self.preview.isolated + "/")):
            return
        rect = self.boundingRect()
        if self.preview.panel_fill and self.node.kind in {"Panel", "ScrollView", "ListView", "PageView"}:
            mode = self.o.get("colorType", 0)
            if mode == 1:
                painter.fillRect(rect, color(self.o, "bgColor", self.o.get("bgColorOpacity", 255)))
            elif mode == 2:
                vx, vy = float(self.o.get("vectorX", 0)), float(self.o.get("vectorY", -1))
                gradient = QLinearGradient(rect.center() - QPointF(vx * self.width / 2, -vy * self.height / 2), rect.center() + QPointF(vx * self.width / 2, -vy * self.height / 2))
                gradient.setColorAt(0, color(self.o, "bgStartColor", self.o.get("bgColorOpacity", 255)))
                gradient.setColorAt(1, color(self.o, "bgEndColor", self.o.get("bgColorOpacity", 255)))
                painter.fillRect(rect, gradient)
        if self.image is not None:
            painter.save()
            if self.o.get("flipX") or self.o.get("flipY"):
                painter.translate(self.width if self.o.get("flipX") else 0, self.height if self.o.get("flipY") else 0)
                painter.scale(-1 if self.o.get("flipX") else 1, -1 if self.o.get("flipY") else 1)
            if self.node.kind == "LoadingBar":
                percent = max(0, min(100, float(self.o.get("percent", 100)))) / 100
                left = self.width * (1 - percent) if self.o.get("direction") == 1 else 0
                painter.setClipRect(QRectF(left, 0, self.width * percent, self.height))
            if self.scale9:
                draw_scale9(painter, self.image, rect, self.o)
            else:
                painter.drawImage(rect, self.image)
            painter.restore()
        elif self.missing:
            painter.fillRect(rect, QColor(155, 30, 72, 70))
            pen = QPen(QColor("#fa638e"), 1, Qt.DashLine); pen.setCosmetic(True)
            painter.setPen(pen); painter.drawRect(rect); painter.drawLine(rect.topLeft(), rect.bottomRight())
        if self.text and self.node.kind in {"Label", "TextField", "Button"}:
            painter.setFont(self.font)
            painter.setPen(color(self.o, "textColor" if self.node.kind == "Button" else "color"))
            horizontal = [Qt.AlignLeft, Qt.AlignHCenter, Qt.AlignRight][max(0, min(2, int(self.o.get("hAlignment", 1 if self.node.kind == "Button" else 0))))]
            vertical = [Qt.AlignTop, Qt.AlignVCenter, Qt.AlignBottom][max(0, min(2, int(self.o.get("vAlignment", 1 if self.node.kind == "Button" else 0))))]
            painter.drawText(rect, horizontal | vertical | Qt.TextWordWrap, self.text)
        if not self.preview.exporting and (option.state & QStyle.State_Selected or self.preview.bounds):
            pen = QPen(QColor("#36d4e8") if option.state & QStyle.State_Selected else QColor(160, 210, 230, 110), 1)
            pen.setCosmetic(True); painter.setPen(pen); painter.setBrush(Qt.NoBrush); painter.drawRect(rect)


class PreviewScene(QGraphicsScene):
    def __init__(self, document, resource_root=None):
        super().__init__()
        self.document = document
        self.resources = Resources(document, resource_root)
        self.node_items = {}
        self.panel_fill, self.show_hidden, self.bounds, self.exporting = True, False, False, False
        self.isolated = None
        self.setSceneRect(0, 0, document.width, document.height)
        self.setBackgroundBrush(QColor("#283340"))
        supported = {"Panel", "Node", "Sprite", "ImageView", "Button", "LoadingBar", "Label", "LabelBMFont", "TextField", "ScrollView", "ListView", "PageView"}

        def build(node, parent=None):
            item = NodeItem(node, self, parent)
            if parent is None:
                self.addItem(item)
            self.node_items[node.uid] = item
            if node.kind not in supported:
                self.resources.issues.add(f"Unsupported widget: {node.kind}; container/bounds only")
            for child in node.children:
                build(child, item)
            return item

        build(document.root)
        self.update_visibility()
        if any(n.options.get("layoutType", 0) for n in document.root.walk()):
            self.resources.issues.add("Authored/percent positions shown; relative/linear layout reflow is not simulated.")
        self.resources.issues.add("Static template: Lua-created actors, runtime state, actions and native input are not shown.")
        self.resources.issues.add("Qt font metrics/wrapping approximate native text layout; button preview uses its normal texture.")

    def font_for(self, options):
        name = options.get("fontFile") or options.get("fontName")
        path = self.resources.resolve(name)
        if path:
            if path not in FONT_FAMILIES:
                font_id = QFontDatabase.addApplicationFont(str(path))
                FONT_FAMILIES[path] = QFontDatabase.applicationFontFamilies(font_id)
            families = FONT_FAMILIES[path]
            font = QFont(families[0]) if families else QFont("Sans Serif")
        else:
            font = QFont(str(name)) if name else QFont()
        font.setPixelSize(max(1, int(options.get("fontSize", 20))))
        return font

    def update_visibility(self):
        for item in self.node_items.values():
            item.setVisible(self.show_hidden or bool(item.o.get("visible", True)))
        self.update()

    def report(self):
        return {"file": str(self.document.path), "format": self.document.format, "canvas": [self.document.width, self.document.height],
                "nodes": len(self.node_items), "visible_nodes": sum(item.isVisible() for item in self.node_items.values()),
                "image_nodes": sum(item.image is not None for item in self.node_items.values()),
                "show_hidden": self.show_hidden, "issues": sorted(self.resources.issues),
                "resources": sorted(map(str, self.resources.used))}

    def export(self, path):
        image = QImage(int(self.document.width), int(self.document.height), QImage.Format_ARGB32_Premultiplied)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.TextAntialiasing)
        self.exporting = True
        try:
            self.render(painter, QRectF(image.rect()), self.sceneRect())
        finally:
            self.exporting = False; painter.end()
        if not image.save(str(path)):
            raise ValueError(f"Could not export PNG: {path}")
