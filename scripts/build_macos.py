#!/usr/bin/env python3
"""Build a self-contained macOS application from this standalone source tree."""
import argparse
import hashlib
import importlib.metadata as metadata
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
NAME = "Cocos UI Preview"


def source_files():
    """Explicit export boundary: never include a parent project or local environment."""
    names = ["pyproject.toml", "uv.lock", ".gitignore", "LICENSE", "NOTICE", "README.md",
             "THIRD_PARTY.md", "CONTRIBUTING.md", "CHANGELOG.md", "MANIFEST.in"]
    files = [ROOT / name for name in names]
    for directory in ["src", "tests", "scripts", "licenses", ".github"]:
        files.extend(p for p in (ROOT / directory).rglob("*") if p.is_file()
                     and "__pycache__" not in p.parts and not any(part.endswith(".egg-info") for part in p.parts)
                     and p.suffix not in {".pyc", ".pyo"} and p.name != ".DS_Store")
    return sorted(files)


def archive_source(destination, version):
    with tarfile.open(destination, "w:gz") as archive:
        for path in source_files():
            archive.add(path, arcname=f"cocos-ui-preview-{version}/{path.relative_to(ROOT)}", recursive=False)


def run(*command, **kwargs):
    subprocess.run(command, check=True, **kwargs)


def build(output):
    if sys.platform != "darwin":
        raise SystemExit("macOS app builds must run on macOS. Source runs on other platforms.")
    version = metadata.version("cocos-ui-preview")
    output = output.resolve(); output.mkdir(parents=True, exist_ok=True)
    work = output / "build"; work.mkdir(exist_ok=True)
    source = output / f"cocos-ui-preview-{version}-source.tar.gz"
    archive_source(source, version)
    legal = work / "legal"; legal.mkdir(exist_ok=True)
    shutil.copy(source, legal / source.name)
    for name in ["LICENSE", "NOTICE", "THIRD_PARTY.md", "uv.lock"]:
        shutil.copy(ROOT / name, legal / name)
    shutil.copytree(ROOT / "licenses", legal / "licenses", dirs_exist_ok=True)
    dependencies = {}
    for name in ["PyQt5", "PyQt5-Qt5", "PyQt5-sip", "Pillow", "PyInstaller"]:
        dist = metadata.distribution(name)
        dependencies[name] = {"version": dist.version,
                              "source": dist.metadata.get_all("Project-URL") or dist.metadata.get("Home-page")}
        for file in dist.files or []:
            label = str(file).lower()
            if "license" in label or "copying" in label or file.name == "METADATA":
                origin = Path(dist.locate_file(file))
                if origin.is_file():
                    target = legal / "dependencies" / name / str(file)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy(origin, target)
    import sysconfig
    stdlib = Path(sysconfig.get_path("stdlib"))
    python_license = next((p for p in [stdlib / "LICENSE.txt", Path(sys.base_prefix) / "LICENSE"] if p.is_file()), None)
    if not python_license:
        raise RuntimeError("Python license text missing; cannot build distribution.")
    shutil.copy(python_license, legal / "Python-LICENSE.txt")
    from PyQt5.QtCore import QT_VERSION_STR, qVersion
    receipt = {"version": version, "architecture": platform.machine(), "python": platform.python_version(),
               "qt_runtime": qVersion(), "qt_compiled": QT_VERSION_STR, "dependencies": dependencies,
               "source_sha256": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in source_files()}}
    (legal / "THIRD_PARTY.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    iconset = work / "AppIcon.iconset"; iconset.mkdir(exist_ok=True)
    from PIL import Image
    with Image.open(ROOT / "src/cocos_ui_preview/assets/icon.png") as icon:
        for size in [16, 32, 128, 256, 512]:
            for scale in [1, 2]:
                icon.resize((size * scale, size * scale), Image.Resampling.LANCZOS).save(
                    iconset / f"icon_{size}x{size}{'@2x' if scale == 2 else ''}.png")
    icon = work / "AppIcon.icns"
    run("iconutil", "-c", "icns", "-o", str(icon), str(iconset))
    entry = work / "entry.py"
    entry.write_text("from cocos_ui_preview.app import main\nraise SystemExit(main())\n")
    info = {"CFBundleDisplayName": NAME, "CFBundleShortVersionString": version,
            "CFBundleVersion": version, "LSMinimumSystemVersion": "12.0",
            "NSHighResolutionCapable": True, "NSHumanReadableCopyright": "GPL-3.0-only · Cocos UI Preview contributors",
            "CFBundleDocumentTypes": [{"CFBundleTypeName": "Cocos Studio UI", "CFBundleTypeRole": "Viewer",
              "LSHandlerRank": "Alternate", "CFBundleTypeExtensions": ["json", "csd"]}]}
    spec = work / "CocosUI.spec"
    spec.write_text(f'''from PyInstaller.utils.hooks import collect_data_files
a = Analysis([{str(entry)!r}], pathex=[{str(ROOT / 'src')!r}],
             datas=collect_data_files('cocos_ui_preview') + [({str(legal)!r}, 'legal')],
             excludes=['numpy', 'tkinter', 'pytest', 'setuptools', 'PyQt5.QtWebEngineWidgets'])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name={NAME!r}, console=False,
          target_arch={platform.machine()!r}, codesign_identity=None)
coll = COLLECT(exe, a.binaries, a.datas, name={NAME!r})
app = BUNDLE(coll, name={NAME + '.app'!r}, icon={str(icon)!r},
             bundle_identifier='io.github.minhnndev.cocos-ui-preview', info_plist={info!r})
''')
    run(sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", str(output),
        "--workpath", str(work / "pyinstaller"), str(spec), cwd=ROOT)
    app = output / f"{NAME}.app"
    run("codesign", "--verify", "--deep", "--strict", str(app))
    zip_path = output / f"cocos-ui-preview-{version}-macos-{platform.machine()}.zip"
    run("ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(app), str(zip_path))
    for path in [zip_path, source]:
        print(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}")
    (output / "SHA256SUMS").write_text("".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n"
                                              for p in [zip_path, source]))
    (output / "build-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(f"Built {app}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts")
    build(parser.parse_args().output)
