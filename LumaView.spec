# -*- mode: python ; coding: utf-8 -*-
"""Run on an Apple Silicon Mac with: python -m PyInstaller LumaView.spec"""
import importlib.metadata
import ast
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_submodules

root = Path(SPECPATH).resolve()
module = ast.parse((root / 'lumaview' / '__init__.py').read_text())
version = next(ast.literal_eval(node.value) for node in module.body
               if isinstance(node, ast.Assign)
               and any(isinstance(target, ast.Name) and target.id == '__version__'
                       for target in node.targets))
staging = root / 'build' / 'packaging'
staging.mkdir(parents=True, exist_ok=True)
entry = staging / 'lumaview_entry.py'
entry.write_text('from lumaview.__main__ import main\nif __name__ == "__main__":\n    raise SystemExit(main())\n')

datas = []
binaries = []
hiddenimports = ['PySide6.QtSvg', 'PySide6.QtPrintSupport', 'PIL.ImageCms']
for package in ('pillow_heif', 'pypdfium2', 'pypdfium2_raw', 'imagecodecs', 'rawpy',
                'numba', 'llvmlite',
                'Vision', 'Quartz', 'Foundation', 'AppKit', 'CoreFoundation',
                'Cocoa', 'objc', 'PyObjCTools'):
    package_data, package_binaries, package_imports = collect_all(package)
    datas.extend(package_data)
    binaries.extend(package_binaries)
    hiddenimports.extend(package_imports)
hiddenimports += collect_submodules('PIL')
hiddenimports += collect_submodules('reportlab')

for folder in ('assets', 'docs'):
    if (root / folder).is_dir():
        datas.append((str(root / folder), folder))
for filename in ('LICENSE', 'THIRD_PARTY.md'):
    datas.append((str(root / filename), '.'))

# Preserve upstream notices, including the bundled C/C++ codec notices.
for dist in importlib.metadata.distributions():
    name = dist.metadata.get('Name', 'unknown')
    for relative in dist.files or []:
        lower = str(relative).lower()
        if any(token in lower for token in ('license', 'licence', 'copying', 'copyright', 'notice')):
            path = Path(dist.locate_file(relative))
            if path.is_file():
                datas.append((str(path), f'third_party_licenses/{name}/{Path(relative).parent}'))

a = Analysis(
    [str(entry)], pathex=[str(root)], binaries=binaries, datas=datas,
    hiddenimports=hiddenimports,
    excludes=['tkinter', 'matplotlib', 'IPython', 'pytest', 'scipy', 'pandas'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name='LumaView',
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=False, argv_emulation=False, target_arch='arm64',
    codesign_identity=None, entitlements_file=None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='LumaView')
app = BUNDLE(
    coll, name='LumaView.app', icon=str(root / 'assets' / 'lumaview.icns'),
    bundle_identifier='org.lumaview.LumaView', version=version,
    info_plist={
        'CFBundleDisplayName':'LumaView',
        'LSMinimumSystemVersion':'14.0',
        'NSHighResolutionCapable':True,
        'LSApplicationCategoryType':'public.app-category.photography',
        'CFBundleDocumentTypes':[
            {'CFBundleTypeName':'Images', 'CFBundleTypeRole':'Editor',
             'LSHandlerRank':'Alternate',
             'LSItemContentTypes':['public.image','com.adobe.pdf','public.svg-image']},
        ],
    },
)
