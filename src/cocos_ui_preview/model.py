"""Cocos Studio document model and explicit resource resolution."""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
from pathlib import Path
import plistlib
import re

from PIL import Image, ImageChops


def numbers(value):
    return [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", str(value))]


@dataclass
class Node:
    uid: str
    kind: str
    options: dict
    children: list[Node] = field(default_factory=list)

    @property
    def name(self):
        return self.options.get("name") or self.kind

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()


@dataclass
class Document:
    path: Path
    data: dict
    root: Node
    width: float
    height: float
    format: str = "legacy-json"
    issues: list[str] = field(default_factory=list)
    source_bytes: bytes | None = None

    @classmethod
    def load(cls, path):
        path = Path(path).resolve()
        if path.suffix.lower() not in {".json", ".csd"}:
            raise ValueError("Open a Cocos Studio JSON or CSD file; CSB decoding is not implemented.")
        if path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError("UI document exceeds the 16 MiB preview limit.")
        source_bytes = path.read_bytes()
        source = source_bytes.decode("utf-8-sig")
        if path.suffix.lower() == ".csd":
            from .studio import load_csd
            data = load_csd(source)
        else:
            data = json.loads(source)
            if isinstance(data, dict) and "widgetTree" not in data:
                from .studio import load_studio_json
                data = load_studio_json(data)
        document = cls.from_data(path, data)
        document.source_bytes = source_bytes
        return document

    @classmethod
    def from_data(cls, path, data):
        """Validate an in-memory document without rereading or writing the source."""
        path = Path(path).resolve()
        if not isinstance(data, dict) or not isinstance(data.get("widgetTree"), dict):
            raise ValueError("Expected a Cocos Studio JSON object with widgetTree.")

        count = 0
        def parse(raw, uid):
            nonlocal count
            count += 1
            if count > 20000 or uid.count("/") > 100:
                raise ValueError("UI document exceeds preview node/depth limits (20000 / 100).")
            if not isinstance(raw, dict) or not isinstance(raw.get("options"), dict):
                raise ValueError(f"Invalid widget at {uid}: missing options")
            if not isinstance(raw.get("children", []), list):
                raise ValueError(f"Invalid children at {uid}: expected a list")
            for key in ("width", "height", "x", "y", "scaleX", "scaleY", "rotation", "opacity",
                        "anchorPointX", "anchorPointY", "sizePercentX", "sizePercentY", "positionPercentX", "positionPercentY"):
                if key in raw["options"] and not math.isfinite(float(raw["options"][key])):
                    raise ValueError(f"Non-finite {key} at {uid}")
            return Node(uid, raw.get("classname", "Unknown"), raw["options"],
                        [parse(child, f"{uid}/{i}") for i, child in enumerate(raw.get("children", []))])

        root = parse(data["widgetTree"], "0")
        width = float(data.get("designWidth") or root.options.get("width") or 1366)
        height = float(data.get("designHeight") or root.options.get("height") or 768)
        if not 0 < width <= 8192 or not 0 < height <= 8192 or width * height > 32_000_000:
            raise ValueError("Design canvas must be positive, at most 8192px per side and 32 megapixels.")
        return cls(path, data, root, width, height, data.get("_format", "legacy-json"), data.get("_issues", []))


class Resources:
    """Resolve exact paths; never guess a texture by basename."""

    def __init__(self, document, resource_root=None):
        self.document = document
        self.roots = [document.path.parent]
        if resource_root:
            self.roots.append(Path(resource_root).resolve())
        # An explicit resource root wins over inferred project roots. No basename search.
        for ancestor in document.path.parents:
            if ancestor.name.lower() in {"res", "resources"}:
                self.roots.extend([ancestor, ancestor.parent])
                break
        self.roots = list(dict.fromkeys(self.roots))
        self.issues = set(document.issues)
        self.used = set()
        self._images = {}
        self._atlases = {}

    def resolve(self, name, relative_to=None):
        if not name:
            return None
        path = Path(str(name).replace("\\", "/"))
        bases = ([Path(relative_to)] if relative_to else []) + self.roots
        candidates = [path] if path.is_absolute() else [base / path for base in bases]
        for candidate in candidates:
            if candidate.is_file():
                result = candidate.resolve()
                self.used.add(result)
                return result
        return None

    def _read(self, path):
        if path not in self._images:
            with Image.open(path) as image:
                self._images[path] = image.convert("RGBA")
        return self._images[path]

    def _atlas(self, path):
        if path not in self._atlases:
            data = plistlib.loads(path.read_bytes())
            metadata = data.get("metadata", {})
            texture = metadata.get("textureFileName") or metadata.get("realTextureFileName") or path.with_suffix(".png").name
            image_path = self.resolve(texture, path.parent)
            if not image_path:
                raise ValueError(f"Missing atlas image: {texture}")
            self._atlases[path] = data, self._read(image_path)
        return self._atlases[path]

    def image(self, reference, owner=""):
        if not isinstance(reference, dict) or not reference.get("path"):
            return None
        name = reference["path"]
        try:
            resource_type = int(reference.get("resourceType", 0))
            if resource_type == 0:
                path = self.resolve(name)
                if not path:
                    raise ValueError(f"Missing texture: {name}")
                return self._read(path)
            if resource_type != 1:
                raise ValueError(f"Unsupported resource type {resource_type}: {name}")
            plists = [reference["plistFile"]] if reference.get("plistFile") else self.document.data.get("textures", [])
            found = []
            for plist in dict.fromkeys(plists):
                path = self.resolve(plist)
                if not path:
                    raise ValueError(f"Missing atlas: {plist}")
                data, atlas = self._atlas(path)
                frames = data.get("frames", {})
                for key, frame in frames.items():
                    if key == name or name in frame.get("aliases", []):
                        found.append((path, data, frame, atlas))
            if len(found) != 1:
                raise ValueError(f"Atlas frame {'ambiguous' if found else 'missing'}: {name}")
            _, data, frame, atlas = found[0]
            return self.extract_frame(atlas, frame, int(data.get("metadata", {}).get("format", 2)))
        except (OSError, ValueError, KeyError, TypeError, OverflowError, Image.DecompressionBombError, plistlib.InvalidFileException) as error:
            self.issues.add(f"{owner}: {error}")
            return None

    @staticmethod
    def extract_frame(atlas, frame, format_id):
        if format_id in (1, 2):
            x, y, w, h = numbers(frame["frame"])
            rotated = bool(frame.get("rotated", False))
            size = numbers(frame.get("sourceSize", f"{{{w},{h}}}"))
            offset = numbers(frame.get("offset", "{0,0}"))
        elif format_id == 3:
            x, y, _, _ = numbers(frame["textureRect"])
            w, h = numbers(frame["spriteSize"])
            rotated = bool(frame.get("textureRotated", False))
            size = numbers(frame["spriteSourceSize"])
            offset = numbers(frame.get("spriteOffset", "{0,0}"))
        else:
            raise ValueError(f"Unsupported plist format {format_id}")
        packed_w, packed_h = (h, w) if rotated else (w, h)
        image = atlas.crop(tuple(round(v) for v in (x, y, x + packed_w, y + packed_h)))
        if rotated:
            image = image.transpose(Image.Transpose.ROTATE_90)
        canvas = Image.new("RGBA", tuple(round(v) for v in size))
        left = round((size[0] - image.width) / 2 + offset[0])
        top = round((size[1] - image.height) / 2 - offset[1])
        canvas.paste(image, (left, top))
        return canvas

    def bitmap_text(self, reference, text, owner):
        path = self.resolve((reference or {}).get("path"))
        if not path:
            self.issues.add(f"{owner}: Missing bitmap font: {(reference or {}).get('path')}")
            return None
        try:
            chars, pages, kernings, line_height = {}, {}, {}, 32
            for line in path.read_text(encoding="utf-8-sig").splitlines():
                values = dict(re.findall(r'(\w+)=\s*("[^"]*"|[^\s]+)', line))
                if line.startswith("common "):
                    line_height = int(values["lineHeight"])
                elif line.startswith("page "):
                    page = self.resolve(values["file"].strip('"'), path.parent)
                    if not page:
                        raise ValueError(f"Missing bitmap page: {values['file']}")
                    pages[int(values["id"])] = self._read(page)
                elif line.startswith("char "):
                    chars[int(values["id"])] = {k: int(v) for k, v in values.items()}
                elif line.startswith("kerning "):
                    kernings[int(values["first"]), int(values["second"])] = int(values["amount"])
            placements, x, y, previous, width = [], 0, 0, None, 0
            for char in text:
                if char == "\n":
                    x, y, previous = 0, y + line_height, None
                    continue
                glyph = chars.get(ord(char))
                if not glyph:
                    self.issues.add(f"{owner}: Bitmap font has no glyph {char!r}")
                    x += line_height // 2
                    continue
                x += kernings.get((previous, ord(char)), 0)
                placements.append((glyph, x + glyph["xoffset"], y + glyph["yoffset"]))
                x += glyph["xadvance"]
                width = max(width, x, placements[-1][1] + glyph["width"])
                previous = ord(char)
            height = max([y + line_height] + [gy + g["height"] for g, _, gy in placements])
            canvas = Image.new("RGBA", (max(1, width), max(1, height)))
            for glyph, gx, gy in placements:
                image = pages[glyph.get("page", 0)].crop((glyph["x"], glyph["y"], glyph["x"] + glyph["width"], glyph["y"] + glyph["height"]))
                canvas.alpha_composite(image, (gx, gy))
            return canvas
        except (OSError, ValueError, KeyError, Image.DecompressionBombError) as error:
            self.issues.add(f"{owner}: Bitmap font: {error}")
            return None


def tinted(image, options):
    color = tuple(max(0, min(255, int(options.get("color" + key, 255)))) for key in "RGB")
    return image if color == (255, 255, 255) else ImageChops.multiply(image, Image.new("RGBA", image.size, color + (255,)))
