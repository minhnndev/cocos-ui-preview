# Cocos UI Preview

An offline, read-only desktop inspector for Cocos2d-x / Cocos Studio UI files.
Open a template, inspect its hierarchy and texture geometry, isolate a subtree,
and export a PNG without starting your game.

**Early release: static preview, not a Cocos runtime or full editor.**

## Install

Download the macOS ZIP from [Releases](https://github.com/minhnndev/cocos-ui-preview/releases),
extract it and drag **Cocos UI Preview.app** to Applications. Python is bundled.
The initial build is Apple Silicon (arm64), locally validated on macOS 26;
its declared minimum is macOS 12, which has not been independently tested.
The app is ad-hoc signed, not Developer ID signed or notarized. For a download
blocked by macOS, review the source/build identity and use the standard
Privacy & Security > Open Anyway flow. Do not disable Gatekeeper globally.

For source use on macOS, Linux or Windows (Python 3.10+):

```sh
pip install .
cocos-ui-preview
cocos-ui-preview path/to/screen.csd --resource-root path/to/Resources
```

Linux/Windows source CI covers offline rendering and parsing; binary releases
and interactive platform acceptance currently cover macOS only.

## Use

- Open UI: legacy `widgetTree` JSON, Studio 2/3 `ObjectData` JSON, or `.csd` XML.
- Resource root: choose your project's `res`/`Resources` root if relative paths need it.
- Select nodes on the canvas or tree for raw normalized properties and bounds.
- Scroll to zoom, drag empty canvas to pan, Fit to reset the view.
- Show hidden reveals template states; Bounds draws geometry; Isolate selection
  draws only the selected subtree while retaining ancestor transforms/clipping.
- Reload picks up source changes. Export PNG saves the current static canvas.
- Finder Open With and dropping a JSON/CSD file open it in the macOS app.

```sh
cocos-ui-preview screen.json --resource-root Resources \
  --export preview.png --report preview.json
```

The report lists missing/ambiguous resources and preview limits. It contains
local paths; review it before sharing. The bundled demo and icon are original,
redistributable fixtures. No game data is included.

## Support boundary

Supported common widgets: Panel, ImageView, Button (normal texture), LoadingBar,
Label, LabelBMFont (text FNT), TextField placeholder, Sprite, Node, and
ScrollView/ListView/PageView containers. Supports hierarchy, anchor/scale/rotation,
authored positions, legacy percent size/position, visibility, clipping, scale9,
tint, flip, progress crop, local textures, and plist formats 1/2/3 (trim/rotation/alias).

CSD/Studio JSON normalizes common properties only. Layout constraints, percent
reflow, nested ProjectNode, advanced widget state, timeline, rich-text effects,
and independent skew are not simulated. Scroll/list/page containers show authored
positions without scrolling or reflow. Unknown widgets are reported as containers.

No CSB decoder, Lua/JS execution, Cocos Creator scene/prefab format, native input,
actors, animation, game state, or native visual equivalence. Qt font metrics and
wrapping can differ from Cocos. Always verify final text fit and runtime UI in game.
Do not open untrusted assets as a security sandbox: image/font libraries still
parse local files and documents can reference absolute paths.

## Develop and build

```sh
uv sync --group build --python 3.12
uv run python -m unittest discover -s tests -v
uv run cocos-ui-preview
uv run python scripts/build_macos.py --output artifacts
```

Build output: `artifacts/Cocos UI Preview.app`, ZIP, source archive, and SHA256SUMS.
Build uses only this repository and locked packages; it never packages an input
project. `Contents/Resources/legal/` carries source, lockfile, and dependency notices.

Smoke the built executable (including its real window, tree selection and export):

```sh
"artifacts/Cocos UI Preview.app/Contents/MacOS/Cocos UI Preview" --smoke-test artifacts/smoke
```

CI tests source on Linux/Windows/macOS and builds macOS artifacts. Public release
publishing is a manual maintainer step after inspection; CI does not upload a
release automatically. See [THIRD_PARTY](THIRD_PARTY.md) for license/source details.

## License

GPL-3.0-only. See [LICENSE](LICENSE), [NOTICE](NOTICE), and [THIRD_PARTY](THIRD_PARTY.md).
Inspired by WangChuan5ef's MIT Cocos UI Visualizer; implemented as an independent
viewer. Not affiliated with Cocos or any game publisher.
