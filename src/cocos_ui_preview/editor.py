"""Typed legacy property editing, reversible commands and conflict-safe writes."""
from __future__ import annotations

import codecs
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
import time

from PyQt5.QtCore import QObject, QIODevice, QSaveFile, pyqtSignal
from PyQt5.QtWidgets import QUndoCommand, QUndoStack

from .model import Document


TEXT = {"Label", "TextField", "Button", "LabelBMFont"}
PANELS = {"Panel", "ScrollView", "ListView", "PageView"}
IMAGES = {"Button", "ImageView", "LoadingBar", "LabelBMFont"}


@dataclass(frozen=True)
class PropertySpec:
    key: str
    label: str
    group: str = "Transform"
    kind: str = "number"
    default: object = 0
    minimum: float = -1_000_000
    maximum: float = 1_000_000
    step: float = 1
    choices: tuple = ()
    kinds: frozenset = frozenset()

    def applies(self, node):
        return not self.kinds or node.kind in self.kinds

    def value(self, options):
        if self.kind == "color":
            return tuple(options.get(self.key + c, 255) for c in "RGB")
        value = options
        for part in self.key.split("."):
            if not isinstance(value, dict) or part not in value:
                return self.default
            value = value[part]
        return value

    def validate(self, value):
        if self.kind in {"number", "integer", "enum"}:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{self.label}: enter a finite number.")
            if not self.minimum <= value <= self.maximum:
                raise ValueError(f"{self.label}: expected {self.minimum} to {self.maximum}.")
            if self.kind in {"integer", "enum"} and value != int(value):
                raise ValueError(f"{self.label}: expected an integer.")
            if self.kind == "enum" and value not in [entry[0] for entry in self.choices]:
                raise ValueError(f"{self.label}: unsupported choice.")
        elif self.kind == "bool" and not isinstance(value, bool):
            raise ValueError(f"{self.label}: expected a boolean.")
        elif self.kind == "color":
            if not isinstance(value, (tuple, list)) or len(value) != 3:
                raise ValueError("Expected RGB color.")
            if any(isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 255 for v in value):
                raise ValueError("Color channels must be integers from 0 to 255.")
        elif self.kind in {"string", "text", "file"} and not isinstance(value, str):
            raise ValueError(f"{self.label}: expected text.")

    def put(self, options, value):
        self.validate(value)
        if self.kind == "color":
            options.update({self.key + c: v for c, v in zip("RGB", value)})
            return
        target = options
        parts = self.key.split(".")
        for part in parts[:-1]:
            if part not in target:
                target[part] = {}
            if not isinstance(target[part], dict):
                raise ValueError(f"{self.label}: unsupported resource structure.")
            target = target[part]
        target[parts[-1]] = value


def specs():
    result = [PropertySpec("name", "Name (Lua lookup key)", "Content", "string", "")]
    for key, label, default, low, high, step in [
        ("x", "Position X", 0, -1e6, 1e6, 1), ("y", "Position Y", 0, -1e6, 1e6, 1),
        ("width", "Width", 0, 0, 8192, 1), ("height", "Height", 0, 0, 8192, 1),
        ("scaleX", "Scale X", 1, -1000, 1000, .1), ("scaleY", "Scale Y", 1, -1000, 1000, .1),
        ("rotation", "Rotation °", 0, -36000, 36000, 1),
        ("anchorPointX", "Anchor X", .5, -1000, 1000, .1),
        ("anchorPointY", "Anchor Y", .5, -1000, 1000, .1),
        ("positionPercentX", "Position ratio X", 0, -1000, 1000, .01),
        ("positionPercentY", "Position ratio Y", 0, -1000, 1000, .01),
        ("sizePercentX", "Size ratio X", 0, 0, 1000, .01),
        ("sizePercentY", "Size ratio Y", 0, 0, 1000, .01),
    ]:
        result.append(PropertySpec(key, label, default=default, minimum=low, maximum=high, step=step))
    for key, label in [("positionType", "Position mode"), ("sizeType", "Size mode")]:
        result.append(PropertySpec(key, label, kind="enum", choices=((0, "Absolute"), (1, "Parent ratio"))))
    result += [PropertySpec("ignoreSize", "Use intrinsic size", kind="bool", default=True, kinds=frozenset(TEXT | IMAGES)),
               PropertySpec("adaptScreen", "Use canvas size", kind="bool", default=False),
               PropertySpec("ZOrder", "Z-order", kind="integer")]
    for key, label, default, kinds in [("visible", "Visible", True, set()), ("flipX", "Flip X", False, set()),
                                      ("flipY", "Flip Y", False, set()), ("clipAble", "Clip children", False, PANELS),
                                      ("scale9Enable", "Scale9", False, {"Button", "ImageView"}),
                                      ("backGroundScale9Enable", "Background scale9", False, PANELS)]:
        result.append(PropertySpec(key, label, "Appearance", "bool", default, kinds=frozenset(kinds)))
    result += [PropertySpec("opacity", "Opacity", "Appearance", "integer", 255, 0, 255),
               PropertySpec("color", "Tint / text color", "Appearance", "color", (255, 255, 255)),
               PropertySpec("textColor", "Button text color", "Appearance", "color", (255, 255, 255), kinds=frozenset({"Button"})),
               PropertySpec("colorType", "Panel fill", "Appearance", "enum", choices=((0, "None"), (1, "Solid"), (2, "Gradient")), kinds=frozenset(PANELS))]
    for key, label in [("bgColor", "Background"), ("bgStartColor", "Gradient start"), ("bgEndColor", "Gradient end")]:
        result.append(PropertySpec(key, label, "Appearance", "color", (255, 255, 255), kinds=frozenset(PANELS)))
    result.append(PropertySpec("bgColorOpacity", "Background opacity", "Appearance", "integer", 255, 0, 255, kinds=frozenset(PANELS)))
    for key, label in [("scale9Width", "Scale9 width"), ("scale9Height", "Scale9 height"),
                       ("capInsetsX", "Cap inset X"), ("capInsetsY", "Cap inset Y"),
                       ("capInsetsWidth", "Cap inset width"), ("capInsetsHeight", "Cap inset height")]:
        result.append(PropertySpec(key, label, "Appearance", minimum=0, maximum=8192, kinds=frozenset(PANELS | {"Button", "ImageView"})))
    result += [PropertySpec("text", "Text", "Content", "text", "", kinds=frozenset(TEXT)),
               PropertySpec("placeHolder", "Placeholder", "Content", "string", "", kinds=frozenset({"TextField"})),
               PropertySpec("fontSize", "Font size", "Content", "integer", 20, 1, 4096, kinds=frozenset(TEXT - {"LabelBMFont"})),
               PropertySpec("fontName", "Font family", "Content", "string", "", kinds=frozenset(TEXT - {"LabelBMFont"})),
               PropertySpec("fontFile", "Font file", "Resources", "file", "", kinds=frozenset(TEXT - {"LabelBMFont"})),
               PropertySpec("percent", "Progress %", "Content", "number", 100, 0, 100, kinds=frozenset({"LoadingBar"})),
               PropertySpec("direction", "Progress direction", "Content", "enum", choices=((0, "Left to right"), (1, "Right to left")), kinds=frozenset({"LoadingBar"}))]
    for key, label, choices in [("hAlignment", "Horizontal align", ((0, "Left"), (1, "Center"), (2, "Right"))),
                                ("vAlignment", "Vertical align", ((0, "Top"), (1, "Center"), (2, "Bottom")))]:
        result.append(PropertySpec(key, label, "Content", "enum", choices=choices, kinds=frozenset(TEXT - {"LabelBMFont"})))
    for key, kinds in [("normalData", {"Button"}), ("fileNameData", {"ImageView", "Sprite", "LabelBMFont"}),
                       ("textureData", {"LoadingBar"}), ("backGroundImageData", PANELS)]:
        for suffix, label, kind, default, choices in [("path", "Image / frame", "file", "", ()),
                                                     ("resourceType", "Resource type", "enum", 0, ((0, "Local file"), (1, "Atlas frame"))),
                                                     ("plistFile", "Atlas plist", "file", "", ())]:
            result.append(PropertySpec(f"{key}.{suffix}", label, "Resources", kind, default, choices=choices, kinds=frozenset(kinds)))
    return {spec.key: spec for spec in result}


SPECS = specs()


def raw_node(data, uid):
    node = data["widgetTree"]
    for part in uid.split("/")[1:]:
        node = node["children"][int(part)]
    return node


class PropertyCommand(QUndoCommand):
    def __init__(self, session, uid, before, after, label, merge_key=None):
        super().__init__(label)
        self.session, self.uid, self.before, self.after = session, uid, before, after
        self.merge_key = merge_key
        self.timestamp = time.monotonic()

    def id(self):
        return 1 if self.merge_key else -1

    def mergeWith(self, other):
        if (self.uid, self.merge_key) != (other.uid, other.merge_key) or other.timestamp - self.timestamp > .6:
            return False
        self.after, self.timestamp = other.after, other.timestamp
        return True

    def redo(self):
        self.session.replace_options(self.uid, self.after)

    def undo(self):
        self.session.replace_options(self.uid, self.before)


class EditorSession(QObject):
    changed = pyqtSignal()

    def __init__(self, document, validator=None):
        super().__init__()
        self.document = document
        self.editable = document.format == "legacy-json" and document.path.suffix.lower() == ".json"
        self.read_only_reason = "Studio JSON/CSD writers are not available."
        if self.editable and document.source_bytes:
            def unique_object(pairs):
                obj = {}
                for key, value in pairs:
                    if key in obj:
                        raise ValueError(f"Duplicate JSON key: {key}")
                    obj[key] = value
                return obj
            def reject_constant(value):
                raise ValueError(f"Nonstandard JSON number: {value}")
            try:
                json.loads(document.source_bytes.decode("utf-8-sig"), object_pairs_hook=unique_object,
                           parse_constant=reject_constant)
            except ValueError as error:
                self.editable = False
                self.read_only_reason = f"Source cannot be saved without data loss: {error}"
        self.baseline = deepcopy(document.data)
        self.original_bytes = document.source_bytes
        self.validator = validator
        self.undo = QUndoStack(self)
        self.undo.setUndoLimit(500)

    @property
    def dirty(self):
        return not self.undo.isClean()

    def replace_options(self, uid, options):
        raw_node(self.document.data, uid)["options"] = deepcopy(options)
        self.document = Document.from_data(self.document.path, self.document.data)
        self.changed.emit()

    def edit(self, uid, key, value=None, reset=False):
        if not self.editable:
            raise ValueError("Studio JSON/CSD remains read-only; source-preserving writers are not implemented.")
        spec = SPECS[key]
        node = next(n for n in self.document.root.walk() if n.uid == uid)
        if not spec.applies(node):
            raise ValueError("This property does not apply to the selected node.")
        before = deepcopy(node.options)
        after = deepcopy(before)
        if reset:
            baseline = raw_node(self.baseline, uid)["options"]
            keys = [spec.key + c for c in "RGB"] if spec.kind == "color" else [spec.key]
            for name in keys:
                parts = name.split(".")
                # Restore the original resource object as a unit only if it was absent.
                if len(parts) > 1 and parts[0] not in baseline:
                    if isinstance(after.get(parts[0]), dict):
                        after[parts[0]].pop(parts[1], None)
                        if not after[parts[0]]:
                            after.pop(parts[0])
                    continue
                source, target = baseline, after
                for part in parts[:-1]:
                    source = source.get(part, {})
                    target = target.setdefault(part, {})
                if parts[-1] in source:
                    target[parts[-1]] = deepcopy(source[parts[-1]])
                else:
                    target.pop(parts[-1], None)
        else:
            spec.put(after, value)
        if before == after:
            return False
        candidate = deepcopy(self.document.data)
        raw_node(candidate, uid)["options"] = after
        doc = Document.from_data(self.document.path, candidate)
        if self.validator:
            self.validator(doc)
        self.undo.push(PropertyCommand(self, uid, before, after, ("Reset " if reset else "Change ") + spec.label,
                                       spec.key if not reset and spec.kind in {"number", "integer", "text"} else None))
        return True

    def save(self, destination=None):
        if not self.editable:
            raise ValueError("Saving this format is not supported.")
        destination = Path(destination or self.document.path).expanduser().resolve()
        source = self.document.path
        if destination.suffix.lower() != ".json":
            raise ValueError("Save legacy templates with the .json extension.")
        # Moving the file can silently break local texture/font/plist references.
        if destination.parent != source.parent:
            raise ValueError("Save As must stay in the source folder to preserve relative resource paths. Copy the project folder to relocate it.")
        if destination == source:
            current = source.read_bytes()  # A missing/unreadable source is a conflict too.
            if self.original_bytes is None or hashlib.sha256(current).digest() != hashlib.sha256(self.original_bytes).digest():
                raise ValueError("The source changed on disk. Save As with a new filename or reload after reviewing your edits.")
        elif destination.exists():
            raise ValueError("Save As needs a new filename; an existing file will not be overwritten.")
        original = (self.original_bytes or b"").decode("utf-8-sig")
        match = re.search(r'\n([ \t]+)"', original)
        indent = match.group(1) if match else 2
        text = json.dumps(self.document.data, ensure_ascii=False, indent=indent, allow_nan=False)
        if "\r\n" in original:
            text = text.replace("\n", "\r\n")
        text += "\r\n" if "\r\n" in original else "\n"
        payload = text.encode("utf-8")
        if self.original_bytes and self.original_bytes.startswith(codecs.BOM_UTF8):
            payload = codecs.BOM_UTF8 + payload
        if len(payload) > 16 * 1024 * 1024:
            raise ValueError("Saved document exceeds the 16 MiB limit.")
        target = QSaveFile(str(destination))
        target.setDirectWriteFallback(False)
        if not target.open(QIODevice.WriteOnly):
            raise OSError(target.errorString())
        if target.write(payload) != len(payload):
            target.cancelWriting()
            raise OSError(target.errorString())
        try:
            if destination == source:
                if source.read_bytes() != self.original_bytes:
                    raise ValueError("The source changed during Save. Your changes remain unsaved.")
            elif destination.exists():
                raise ValueError("The Save As destination appeared during Save. Choose another filename.")
        except (OSError, ValueError):
            target.cancelWriting()
            raise
        if not target.commit():
            raise OSError(target.errorString())
        self.document.path = destination
        self.original_bytes = payload
        self.baseline = deepcopy(self.document.data)
        self.undo.setClean()
        return destination
