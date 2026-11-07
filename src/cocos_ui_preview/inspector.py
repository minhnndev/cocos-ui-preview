"""Property controls selected by field type and widget kind."""
import os
from pathlib import Path

from PyQt5.QtCore import Qt, QSignalBlocker, pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox,
                            QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                            QPlainTextEdit, QPushButton, QScrollArea, QSlider,
                            QSpinBox, QTabWidget, QVBoxLayout, QWidget)

from .editor import SPECS


class PropertyInspector(QWidget):
    edited = pyqtSignal(str, object)
    reset = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0)
        self.notice = QLabel("Select a node to edit its authored properties.")
        self.notice.setWordWrap(True); self.notice.setMinimumHeight(48)
        self.notice.setAlignment(Qt.AlignTop | Qt.AlignLeft); layout.addWidget(self.notice)
        self.tabs = QTabWidget(); layout.addWidget(self.tabs)
        self.controls = {}; self.resets = {}; self.sliders = {}
        self.uid = None; self.path = None; self.editable = False

    def clear(self):
        self.uid = None
        self.notice.setText("Select a node to edit its authored properties.")
        while self.tabs.count():
            widget = self.tabs.widget(0); self.tabs.removeTab(0); widget.deleteLater()
        self.controls.clear(); self.resets.clear(); self.sliders.clear()

    def bind(self, node, path, editable, read_only_reason="Studio JSON/CSD writers are not available."):
        signature = (node.uid, node.kind, editable)
        self.path, self.editable = Path(path), editable
        if self.uid != signature:
            tab = self.tabs.currentIndex()
            self.clear(); self.uid = signature
            for group in ["Transform", "Content", "Appearance", "Resources"]:
                body = QWidget(); form = QFormLayout(body); form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
                for spec in SPECS.values():
                    if spec.group != group or not spec.applies(node):
                        continue
                    row = QWidget(); row_layout = QHBoxLayout(row); row_layout.setContentsMargins(0, 0, 0, 0)
                    control = self.make_control(spec, row_layout)
                    control.setObjectName("property_" + spec.key)
                    self.controls[spec.key] = control
                    reset = QPushButton("↺"); reset.setFixedWidth(26)
                    reset.setToolTip("Restore this field to the last loaded/saved value; Undo restores your edit.")
                    reset.clicked.connect(lambda checked=False, key=spec.key: self.reset.emit(key))
                    self.resets[spec.key] = reset; row_layout.addWidget(reset)
                    form.addRow(spec.label, row)
                scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(body)
                self.tabs.addTab(scroll, group)
            self.tabs.setCurrentIndex(max(0, tab))
        self.notice.setText("Editing legacy JSON · preview updates immediately. Name changes can affect Lua lookups."
                            if editable else "Read only · " + read_only_reason)
        for key, control in self.controls.items():
            spec = SPECS[key]
            value = spec.value(node.options)
            if key in {"scale9Width", "scale9Height"} and key not in node.options:
                value = node.options.get("width" if key.endswith("Width") else "height", 0)
            if key in {"hAlignment", "vAlignment"} and key not in node.options and node.kind == "Button":
                value = 1
            valid = True
            try:
                spec.validate(value)
            except (ValueError, TypeError, OverflowError):
                valid = False
            blocker = QSignalBlocker(control)
            if valid:
                if spec.kind == "bool":
                    control.setChecked(value)
                elif spec.kind in {"number", "integer"}:
                    control.setValue(int(value) if spec.kind == "integer" else value)
                elif spec.kind == "enum":
                    control.setCurrentIndex(control.findData(value))
                elif spec.kind == "color":
                    control.setText("#%02X%02X%02X" % tuple(value))
                    control.setStyleSheet(f"border-left: 14px solid {control.text()}; padding: 5px;")
                elif spec.kind == "text":
                    if control.toPlainText() != value:
                        control.setPlainText(value)
                else:
                    control.setText(value)
                    control.setModified(False)
            else:
                control.setToolTip(f"Unsupported authored value: {value!r}; inspect the raw data.")
            del blocker
            enabled, reason = self.active(spec, node)
            control.parentWidget().setEnabled(editable and valid and enabled)
            control.parentWidget().setToolTip(reason if valid else f"Unsupported authored value: {value!r}")
            if key in self.sliders and valid:
                slider = self.sliders[key]; blocker = QSignalBlocker(slider); slider.setValue(round(value)); del blocker

    @staticmethod
    def active(spec, node):
        o = node.options; key = spec.key
        scale9 = o.get("scale9Enable") or o.get("backGroundScale9Enable")
        if key in {"x", "y"} and o.get("positionType") == 1:
            return False, "Position comes from the parent ratios. Change Position mode to edit pixels."
        if key.startswith("positionPercent") and o.get("positionType", 0) != 1:
            return False, "Choose Parent ratio position mode to use this field."
        if key.startswith("sizePercent") and o.get("sizeType", 0) != 1:
            return False, "Choose Parent ratio size mode to use this field."
        if key in {"width", "height"}:
            if o.get("adaptScreen") or o.get("sizeType") == 1 or scale9 or (o.get("ignoreSize", True) and node.kind in {"Button", "ImageView", "LoadingBar", "LabelBMFont", "Label", "TextField"}):
                return False, "Size is controlled by canvas, parent ratio, scale9 dimensions or intrinsic content."
        if (key.startswith("capInsets") or key in {"scale9Width", "scale9Height"}) and not scale9:
            return False, "Enable Scale9 to use this field."
        reference = o.get(key.split(".")[0])
        if key.endswith(".plistFile") and (not isinstance(reference, dict) or reference.get("resourceType", 0) != 1):
            return False, "Choose Atlas frame resource type to use a plist."
        if key == "clipAble" and node.kind in {"ScrollView", "ListView", "PageView"}:
            return False, "This container always clips its children in the preview."
        if key == "fontName" and o.get("fontFile"):
            return False, "The font file overrides the font family. Clear Font file first."
        return True, ""

    def make_control(self, spec, layout):
        key = spec.key
        if spec.kind in {"integer", "number"}:
            control = QSpinBox() if spec.kind == "integer" else QDoubleSpinBox()
            if spec.kind == "number":
                control.setDecimals(4)
            control.setRange(spec.minimum if spec.kind == "number" else int(spec.minimum),
                             spec.maximum if spec.kind == "number" else int(spec.maximum))
            control.setSingleStep(spec.step if spec.kind == "number" else int(spec.step))
            control.setKeyboardTracking(False)
            control.valueChanged.connect(lambda value: self.edited.emit(key, value))
            if key in {"opacity", "percent", "bgColorOpacity"}:
                slider = QSlider(Qt.Horizontal); slider.setRange(int(spec.minimum), int(spec.maximum))
                slider.valueChanged.connect(lambda value: self.edited.emit(key, value))
                self.sliders[key] = slider; layout.addWidget(slider, 1)
        elif spec.kind == "bool":
            control = QCheckBox(); control.toggled.connect(lambda value: self.edited.emit(key, value))
        elif spec.kind == "enum":
            control = QComboBox()
            for value, label in spec.choices:
                control.addItem(label, value)
            control.activated.connect(lambda index: self.edited.emit(key, control.itemData(index)))
        elif spec.kind == "color":
            control = QPushButton()
            control.clicked.connect(lambda: self.pick_color(spec, control))
        elif spec.kind == "text":
            control = QPlainTextEdit(); control.setMaximumHeight(95)
            control.textChanged.connect(lambda: self.edited.emit(key, control.toPlainText()))
        else:
            control = QLineEdit(); control.editingFinished.connect(lambda: self.edited.emit(key, control.text()) if control.isModified() else None)
        layout.addWidget(control, 1)
        if spec.kind == "file":
            browse = QPushButton("…"); browse.setFixedWidth(26)
            browse.clicked.connect(lambda: self.pick_file(spec, control)); layout.addWidget(browse)
        return control

    def pick_color(self, spec, control):
        color = QColorDialog.getColor(QColor(control.text()), self, spec.label)
        if color.isValid():
            self.edited.emit(spec.key, (color.red(), color.green(), color.blue()))

    def pick_file(self, spec, control):
        path, _ = QFileDialog.getOpenFileName(self, spec.label, str(self.path.parent))
        if path:
            # A relative reference keeps the template portable with its project.
            relative = Path(os.path.relpath(path, self.path.parent)).as_posix()
            control.setText(relative); self.edited.emit(spec.key, relative)
