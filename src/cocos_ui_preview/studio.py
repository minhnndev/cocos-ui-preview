"""Normalize Cocos Studio 2/3 ObjectData JSON and CSD XML, without executing code.

Property names follow Cocos Studio's public document schema. No engine is bundled.
"""
import xml.etree.ElementTree as ET


KINDS = {"GameLayerObjectData": "Panel", "GameNodeObjectData": "Node",
         "SingleNodeObjectData": "Node", "PanelObjectData": "Panel",
         "ButtonObjectData": "Button", "ImageViewObjectData": "ImageView",
         "TextObjectData": "Label", "TextBMFontObjectData": "LabelBMFont",
         "TextFieldObjectData": "TextField", "LoadingBarObjectData": "LoadingBar",
         "ScrollViewObjectData": "ScrollView", "ListViewObjectData": "ListView",
         "PageViewObjectData": "PageView", "SpriteObjectData": "Sprite"}
LAYOUTS = {"Panel", "ScrollView", "ListView", "PageView"}


def truth(value):
    return value is True or str(value).lower() in {"true", "1"}


def resource(data):
    if not isinstance(data, dict):
        return {}
    return {"path": data.get("Path", ""), "plistFile": data.get("Plist", ""),
            "resourceType": 1 if data.get("Type") in {"PlistSubImage", "MarkedSubImage"} else 0}


def normalize(raw, issues, depth=0):
    if depth > 100 or not isinstance(raw, dict):
        raise ValueError("Invalid or excessively deep ObjectData tree.")
    kind = KINDS.get(raw.get("ctype"), raw.get("ctype", "Unknown"))
    size = raw.get("Size", {})
    anchor = raw.get("AnchorPoint", {})
    pos = raw.get("Position", {})
    scale = raw.get("Scale", {})
    o = {"name": raw.get("Name", kind), "width": float(size.get("X", 0)),
         "height": float(size.get("Y", 0)), "x": float(pos.get("X", 0)),
         "y": float(pos.get("Y", 0)), "anchorPointX": float(anchor.get("ScaleX", 0)),
         "anchorPointY": float(anchor.get("ScaleY", 0)),
         "scaleX": float(scale.get("ScaleX", 1)), "scaleY": float(scale.get("ScaleY", 1)),
         "visible": truth(raw.get("VisibleForFrame", True)), "opacity": float(raw.get("Alpha", 255)),
         "ZOrder": float(raw.get("ZOrder", 0)), "ignoreSize": False,
         "rotation": float(raw.get("RotationSkewX", 0)),
         "flipX": truth(raw.get("FlipX", False)), "flipY": truth(raw.get("FlipY", False)),
         "scale9Enable": truth(raw.get("Scale9Enable", False))}
    ry = float(raw.get("RotationSkewY", o["rotation"]))
    if ry != o["rotation"]:
        issues.add(f"{o['name']}: separate RotationSkewX/Y not simulated; using X rotation.")
    for prefix, key in [("color", "CColor"), ("textColor", "TextColor"),
                        ("bgColor", "SingleColor"), ("bgStartColor", "FirstColor"), ("bgEndColor", "EndColor")]:
        for channel in "RGB":
            if channel in raw.get(key, {}):
                o[prefix + channel] = int(raw[key][channel])
    mappings = {"FontSize": "fontSize", "ButtonText": "text", "LabelText": "text",
                "PlaceHolderText": "placeHolder", "MaxLengthText": "maxLength",
                "Scale9OriginX": "capInsetsX", "Scale9OriginY": "capInsetsY",
                "Scale9Width": "capInsetsWidth", "Scale9Height": "capInsetsHeight",
                "ComboBoxIndex": "colorType", "BackColorAlpha": "bgColorOpacity",
                "PercentInfo": "percent", "ProgressInfo": "percent", "ProgressType": "direction",
                "HorizontalAlignmentType": "hAlignment", "VerticalAlignmentType": "vAlignment",
                "InnerNodeSize": "_innerSize"}
    for source, target in mappings.items():
        if source in raw:
            o[target] = raw[source]
    for key in ["fontSize", "capInsetsX", "capInsetsY", "capInsetsWidth", "capInsetsHeight",
                "colorType", "bgColorOpacity", "percent", "direction"]:
        if key in o:
            o[key] = float(o[key])
    for key, values in [("hAlignment", {"HT_Left": 0, "HT_Center": 1, "HT_Right": 2}),
                        ("vAlignment", {"VT_Top": 0, "VT_Center": 1, "VT_Bottom": 2})]:
        if key in o:
            o[key] = values.get(o[key], o[key])
    o["clipAble"] = truth(raw.get("ClipAble", False))
    for axis in "XY":
        if axis in raw.get("ColorVector", {}):
            o["vector" + axis] = float(raw["ColorVector"][axis])
    inner = o.pop("_innerSize", raw.get("InnerNodeSize", {}))
    if isinstance(inner, dict):
        o["innerWidth"], o["innerHeight"] = float(inner.get("Width", inner.get("X", o["width"]))), float(inner.get("Height", inner.get("Y", o["height"])))
    for source, target in [("NormalFileData", "normalData"), ("FileData", "fileNameData"),
                           ("ImageFileData", "backGroundImageData"), ("FontResource", "fileNameData")]:
        if source in raw:
            o[target] = resource(raw[source])
    if kind == "LoadingBar":
        o["textureData"] = o.pop("fileNameData", {})
    if raw.get("FontResource", {}).get("Path") and kind != "LabelBMFont":
        o["fontFile"] = raw["FontResource"]["Path"]
        o.pop("fileNameData", None)
    if raw.get("FontName"):
        o["fontName"] = raw["FontName"]
    if kind == "Label":
        o["areaWidth"], o["areaHeight"] = o["width"], o["height"]
    if kind == "Panel":
        o["backGroundScale9Enable"] = o.pop("scale9Enable")
    if any(raw.get(k) for k in ["PrePosition", "PreSize", "LayoutComponent", "LayoutComponentEnabled"]):
        issues.add("Studio layout constraints/PrePosition/PreSize are not reflowed; authored Position/Size shown.")
    if raw.get("ctype") == "ProjectNodeObjectData":
        issues.add(f"{o['name']}: nested project nodes are not expanded.")
    children = raw.get("Children", [])
    if not isinstance(children, list):
        raise ValueError("ObjectData.Children must be a list.")
    return {"classname": kind, "options": o,
            "children": [normalize(c, issues, depth + 1) for c in children]}


def normalized_document(raw, format_name):
    issues = set()
    tree = normalize(raw, issues)
    return {"widgetTree": tree, "designWidth": tree["options"]["width"],
            "designHeight": tree["options"]["height"], "_format": format_name,
            "_issues": sorted(issues), "textures": []}


def load_studio_json(data):
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object.")
    container = data.get("Content", data)
    if isinstance(container, dict):
        container = container.get("Content", container)
    raw = container.get("ObjectData") if isinstance(container, dict) else None
    if not isinstance(raw, dict):
        raise ValueError("Expected legacy widgetTree or Cocos Studio Content/ObjectData JSON.")
    return normalized_document(raw, "studio-json")


def load_csd(source):
    # ElementTree does not fetch external entities. Reject declarations explicitly.
    if "<!DOCTYPE" in source.upper() or "<!ENTITY" in source.upper():
        raise ValueError("CSD entity/DOCTYPE declarations are not supported.")
    try:
        root = ET.fromstring(source)
    except ET.ParseError as error:
        raise ValueError(f"Invalid CSD XML: {error}") from error
    element = root.find(".//ObjectData")
    if element is None:
        raise ValueError("Expected GameFile/Content/Content/ObjectData in CSD.")

    def convert(element, depth=0):
        if depth > 100:
            raise ValueError("CSD exceeds the preview depth limit.")
        raw = dict(element.attrib)
        for child in element:
            if child.tag == "Children":
                raw["Children"] = [convert(c, depth + 1) for c in child]
            else:
                raw[child.tag] = dict(child.attrib)
        return raw

    return normalized_document(convert(element), "csd")
