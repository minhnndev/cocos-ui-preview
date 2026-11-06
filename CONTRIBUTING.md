# Contributing

Use Python 3.12 and uv:

```sh
uv sync --group build --python 3.12
uv run python -m unittest discover -s tests -v
uv run cocos-ui-preview
```

Keep parsing separate from rendering. Add small authored fixtures and pixel or
geometry assertions for reader changes. Do not contribute proprietary game
assets, credentials, saves, or screenshots without redistribution rights.

CSD/Studio JSON support is a documented subset. Report unsupported properties
explicitly; do not claim native rendering parity. macOS packaging is validated
separately from source tests. See README for build and smoke commands.

Contributions are accepted under GPL-3.0-only, the project license.
