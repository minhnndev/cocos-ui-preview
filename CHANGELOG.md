# Changelog

## 0.2.0

- Typed legacy JSON property editor: numeric stepping, booleans, enums, live
  text, RGB color pickers, resource paths, scale9 and opacity/progress sliders.
- Live scene rebuild preserves selection, zoom, tree expansion and isolation.
- Undo/redo, field reset, dirty state and unsaved-document prompts.
- Atomic Save with external-change detection; Save As to a new filename in the
  same folder. Unknown JSON members retained; Studio JSON/CSD stays view-only.
- Writable user demo copy; packaged original/resources remain intact by default.
- Editing/roundtrip/failure regressions and an executable editor smoke scenario.

## 0.1.0

- Read-only legacy Cocos Studio widgetTree JSON, Studio ObjectData JSON and CSD XML preview.
- Node tree, properties, resource roots, visibility, bounds, isolate and PNG export.
- Images, plist frames, bitmap fonts, scale9 and common UI widgets.
- Independent demo assets and standalone macOS app packaging.
- Explicit limits for native runtime, layout reflow, font metrics and CSB.
