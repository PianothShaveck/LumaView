# Third-party components

LumaView does not contain IrfanView source code, plug-ins, icons, or branding. IrfanView is a separate product.

The Python dependencies below are installed by the source installer and included in bundled releases. Their license notices remain applicable independently of LumaView's MIT license.

| Component | Purpose | Project and license information |
|---|---|---|
| CPython | Runtime | [Python license](https://docs.python.org/3/license.html) |
| uv / python-build-standalone | Source installation | [uv](https://github.com/astral-sh/uv), [Python builds](https://github.com/astral-sh/python-build-standalone) |
| PySide6 / Qt | User interface | [Qt for Python licensing](https://doc.qt.io/qtforpython-6/licenses.html) |
| Pillow | Image processing and codecs | [Pillow](https://github.com/python-pillow/Pillow) |
| NumPy | Pixel calculations | [NumPy](https://github.com/numpy/numpy) |
| Numba / llvmlite | Native monochrome processing | [Numba](https://github.com/numba/numba), [llvmlite](https://github.com/numba/llvmlite) |
| pypdfium2 / PDFium | PDF rendering | [pypdfium2 licensing](https://github.com/pypdfium2-team/pypdfium2#licensing) |
| ReportLab | PDF output | [ReportLab](https://pypi.org/project/reportlab/) |
| pillow-heif / libheif | HEIC images | [pillow-heif](https://github.com/bigcat88/pillow_heif), [libheif](https://github.com/strukturag/libheif) |
| imagecodecs | Additional formats | [imagecodecs](https://github.com/cgohlke/imagecodecs) |
| rawpy / LibRaw | Camera RAW decoding | [rawpy](https://github.com/letmaik/rawpy), [LibRaw](https://www.libraw.org/about#license) |
| PyObjC | Apple Vision OCR integration | [PyObjC](https://github.com/ronaldoussoren/pyobjc) |
| PyInstaller | App packaging | [PyInstaller license and exception](https://pyinstaller.org/en/stable/license.html) |

Binary wheels also contain third-party codec libraries with their own terms. Bundled builds copy available license, copyright, and notice files from the build environment to `LumaView.app/Contents/Resources/third_party_licenses`. In a source installation, those records remain in the installed packages under `~/Library/Application Support/LumaView/runtime/`.

Qt and the codec libraries are dynamically bundled. The source, pinned dependencies, and packaging recipe are included so developers can rebuild against modified compatible libraries. Redistributors must retain dependency notices and satisfy the terms of the exact binary libraries they ship.

Optional OptiPNG and Ghostscript are separate installations. They are not downloaded or bundled by LumaView.
