# Dependencies and source

The application is GPL-3.0-only because it uses the GPL edition of PyQt5.
Project source is provided with each release. The build uses shared Qt
frameworks; no commercial PyQt license is assumed.

| Component | License | Source / license |
|---|---|---|
| Python 3.12 | PSF-2.0 and included notices | https://www.python.org/downloads/source/ |
| PyQt5 | GPL-3.0 | https://www.riverbankcomputing.com/software/pyqt/download |
| Qt 5 (PyQt5-Qt5 wheels) | LGPL-3.0 and Qt third-party notices | https://download.qt.io/archive/qt/5.15/ |
| PyQt5-sip | SIP / GPL-2.0-or-later / GPL-3.0-or-later | https://github.com/Python-SIP/sip |
| Pillow and bundled codecs | MIT-CMU plus included third-party notices | https://github.com/python-pillow/Pillow |
| PyInstaller bootloader | GPL-2.0-or-later with exception | https://github.com/pyinstaller/pyinstaller |
| Cocos2d-x semantics / attribution | MIT | https://github.com/cocos2d/cocos2d-x |
| Upstream visualizer inspiration | MIT | https://github.com/WangChuan5ef/cocos-ui-visualizer |

The exact package versions and downloads are in `uv.lock`. Each macOS build
includes `Contents/Resources/legal/` containing project source, the lockfile,
installed package metadata and license texts. `THIRD_PARTY.json` records exact
package versions and published source locations for that build. Original
library license files are also retained by PyInstaller collection.

There is no Cocos engine binary, game font, game UI JSON, save data, telemetry
or network client in the distribution. Input documents and referenced assets
are read locally. They are not copied into releases.
