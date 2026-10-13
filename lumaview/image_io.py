"""Image codecs and atomic exports.

Pillow handles raster formats; PDFium only rasterizes PDF input. PDF export is
implemented with ReportLab. Optional codecs are advertised only when present.
No operation changes the ImageDocument passed by the caller.
"""
from __future__ import annotations

from functools import lru_cache
from copy import deepcopy
from io import BytesIO
from pathlib import Path
import gzip
import importlib.util
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
from typing import Any

from PIL import Image, ImageColor, ImageOps, JpegImagePlugin, PngImagePlugin

from .model import ImageDocument, FORMAT_LABELS, default_format

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    pillow_heif = None

Image.init()

RAW_EXTENSIONS = {'.dng', '.cr2', '.cr3', '.nef', '.arw', '.orf', '.rw2', '.raf', '.pef'}
_ALIASES = {'JPG': 'JPEG', 'JPE': 'JPEG', 'TIF': 'TIFF', 'JP2': 'JPEG2000', 'JLS': 'JPEGLS',
            'J2K': 'JPEG2000', 'HEIC': 'HEIF', 'PNM': 'PPM'}
_MULTIPAGE = {'GIF', 'TIFF', 'PDF', 'PNG', 'WEBP', 'AVIF', 'HEIF'}
_PDFIUM_LOCK = threading.RLock()


def normalize_format(fmt: str) -> str:
    key = str(fmt).upper().lstrip('.')
    return _ALIASES.get(key, key)


def find_executable(name: str) -> str | None:
    """Find optional tools even when Finder launches with a minimal PATH."""
    located = shutil.which(name)
    if located:
        return located
    if sys.platform == 'darwin':
        for directory in ('/opt/homebrew/bin', '/usr/local/bin', '/usr/bin'):
            candidate = Path(directory) / name
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
    return None


def _module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


@lru_cache(maxsize=1)
def _formats() -> tuple[str, ...]:
    result = []
    for fmt in FORMAT_LABELS:
        if fmt == 'PDF':
            if _module('reportlab'):
                result.append(fmt)
            continue
        if fmt == 'JXL':
            if _module('imagecodecs'):
                import imagecodecs
                if imagecodecs.JPEGXL.available:
                    result.append(fmt)
            continue
        if fmt == 'JPEGLS':
            if _module('imagecodecs'):
                import imagecodecs
                if imagecodecs.JPEGLS.available:
                    result.append(fmt)
            continue
        if fmt == 'RAW':
            result.append(fmt)
            continue
        encoder = 'PPM' if fmt in {'PBM', 'PGM'} else fmt
        if encoder not in Image.SAVE:
            continue
        # Registry entries may exist even when their native codec is absent.
        try:
            mode = {'PBM': '1', 'PGM': 'L', 'GIF': 'P'}.get(fmt, 'RGB')
            Image.new(mode, (32, 32)).save(BytesIO(), format=encoder)
        except (OSError, ValueError, KeyError, RuntimeError):
            continue
        result.append(fmt)
    return tuple(result)


def available_formats() -> list[str]:
    return list(_formats())


def supported_input_extensions() -> set[str]:
    extensions = {ext.lower() for ext, codec in Image.registered_extensions().items()
                  if codec in Image.OPEN}
    extensions.update({'.pld', '.plb'})
    if _module('pypdfium2'):
        extensions.add('.pdf')
    if _module('PySide6.QtSvg'):
        extensions.update({'.svg', '.svgz'})
    if 'JXL' in available_formats():
        extensions.add('.jxl')
    if 'JPEGLS' in available_formats():
        extensions.add('.jls')
    if _module('rawpy'):
        extensions.update(RAW_EXTENSIONS)
    # EPS is usable only when an external interpreter is installed.
    if not find_executable('gs'):
        extensions.difference_update({'.eps', '.ps'})
    return extensions


def _dpi(value: Any, fallback=(72.0, 72.0)) -> tuple[float, float]:
    try:
        values = tuple(float(v) for v in value)
        if len(values) == 2 and all(math.isfinite(v) and v > 0 for v in values):
            return values
    except (ValueError, TypeError):
        pass
    return fallback


def _normalized_xmp(value: bytes | str) -> bytes:
    data = value.encode('utf-8') if isinstance(value, str) else value
    # Pixels have been oriented on import, so preserving an old orientation
    # would cause other viewers to rotate them a second time.
    data = re.sub(rb'(tiff:Orientation\s*=\s*[\"\'])[1-8]([\"\'])', rb'\g<1>1\2', data)
    return re.sub(rb'(<tiff:Orientation>)[1-8](</tiff:Orientation>)', rb'\g<1>1\2', data)


def _metadata(im: Image.Image) -> dict:
    info = im.info
    md = {'source_format': im.format or ''}
    for key in ('icc_profile', 'comment', 'exif', 'xmp'):
        if info.get(key):
            md[key] = info[key]
    if not md.get('xmp') and info.get('XML:com.adobe.xmp'):
        md['xmp'] = info['XML:com.adobe.xmp']
    if md.get('xmp'):
        md['xmp'] = _normalized_xmp(md['xmp'])
    original_exif = im.getexif()
    if original_exif:
        exif = Image.Exif()
        exif.load(original_exif.tobytes())
        if 274 in exif:
            exif[274] = 1
        md['exif'] = exif.tobytes()
    if im.format == 'JPEG':
        md['jpeg_qtables'] = getattr(im, 'quantization', None)
        md['jpeg_subsampling'] = JpegImagePlugin.get_sampling(im)
        md['jpeg_iptc_segments'] = [bytes(data) for marker, data in getattr(im, 'applist', [])
                                    if marker == 'APP13' and data.startswith(b'Photoshop 3.0\x00')]
    if im.format == 'PNG':
        md['png_text'] = {k: v for k, v in getattr(im, 'text', {}).items()
                          if k not in {'XML:com.adobe.xmp', 'Comment', 'Description'}}
        if not md.get('comment'):
            md['comment'] = info.get('Comment', info.get('Description', ''))
    if im.format == 'TIFF':
        tags = im.tag_v2
        if tags.get(700):
            md['xmp'] = _normalized_xmp(bytes(tags[700]))
        if tags.get(33723):
            val = tags[33723]
            md['tiff_iptc'] = bytes(val) if isinstance(val, (bytes, bytearray)) else val
        if tags.get(270):
            md['comment'] = tags[270]
    return md


def load_document(path: str | Path, pdf_dpi: float = 96, all_pages: bool = True,
                  password: str | None = None, svg_size: tuple[int, int] = (1024, 800),
                  svg_keep_aspect: bool = False) -> ImageDocument:
    path = Path(path)
    ext = path.suffix.lower()
    if ext in {'.pld', '.plb'}:
        return _load_photoline_preview(path)
    if ext == '.pdf':
        return _load_pdf(path, pdf_dpi, all_pages, password)
    if ext in {'.svg', '.svgz'}:
        return _load_svg(path, svg_size, svg_keep_aspect)
    if ext == '.jxl':
        if not _module('imagecodecs'):
            raise RuntimeError('JPEG XL requires imagecodecs. Run the installer again.')
        import imagecodecs
        import numpy as np
        pixels = imagecodecs.jpegxl_decode(path.read_bytes())
        if pixels.dtype == np.uint16 and pixels.ndim == 3:
            pixels = (pixels >> 8).astype(np.uint8)
            warnings = ['16-bit JPEG XL was converted to 8-bit per channel.']
        else:
            warnings = []
        return ImageDocument([Image.fromarray(pixels)], path,
                             {'source_format': 'JXL', 'warnings': warnings})
    if ext == '.jls':
        if not _module('imagecodecs'):
            raise RuntimeError('JPEG-LS requires imagecodecs. Run the installer again.')
        import imagecodecs
        pixels = imagecodecs.jpegls_decode(path.read_bytes())
        return ImageDocument([Image.fromarray(pixels)], path, {'source_format': 'JPEGLS'})
    if ext in RAW_EXTENSIONS:
        if not _module('rawpy'):
            raise RuntimeError('Camera RAW files require rawpy. Run the installer again.')
        import rawpy
        with rawpy.imread(str(path)) as raw:
            pixels = raw.postprocess(use_camera_wb=True, output_bps=8)
        return ImageDocument([Image.fromarray(pixels)], path, {'source_format': 'CAMERA_RAW'})
    if ext in {'.eps', '.ps'}:
        ghostscript = find_executable('gs')
        if not ghostscript:
            raise RuntimeError('EPS/PostScript input requires Ghostscript (brew install ghostscript).')
        from PIL import EpsImagePlugin
        EpsImagePlugin.gs_binary = ghostscript
    frames, durations = [], []
    with Image.open(path) as original:
        # Load before extracting PNG text because ancillary chunks may follow IDAT.
        original.load()
        md = _metadata(original)
        dpi = _dpi(original.info.get('dpi'))
        loop = int(original.info.get('loop', 0))
        frame_count = getattr(original, 'n_frames', 1) if all_pages else 1
        for index in range(frame_count):
            original.seek(index)
            original.load()
            frame = ImageOps.exif_transpose(original)
            if frame_count > 1 and original.format in {'GIF', 'PNG', 'WEBP'}:
                frame = frame.convert('RGBA')
            frames.append(frame.copy())
            durations.append(int(original.info.get('duration', 100)))
        md['frame_count'] = getattr(original, 'n_frames', 1)
    return ImageDocument(frames, path, md, durations, loop, dpi)


def _load_photoline_preview(path: Path) -> ImageDocument:
    """Read PhotoLine's first embedded preview, never claim to render layers."""
    with path.open('rb') as stream:
        # The preview precedes layer data. Bound both the signature search and
        # the decode buffer; later embedded layer images are not previews.
        payload = stream.read(16 * 1024 * 1024)
    is_cache = payload.startswith(b'Photo Line Browsefile\x00')
    if not is_cache and not payload.startswith(b'Photo Line Document\x00'):
        raise ValueError('Not a recognized PhotoLine document or browser cache.')
    signatures = (b'\xff\xd8\xff', b'\x89PNG\r\n\x1a\n')
    offsets = [payload.find(signature, 20, 65536) for signature in signatures]
    offsets = [offset for offset in offsets if offset >= 0]
    if not offsets:
        raise ValueError('No readable embedded PhotoLine preview. Export the document from PhotoLine.')
    try:
        with Image.open(BytesIO(payload[min(offsets):])) as preview:
            if preview.format not in {'JPEG', 'PNG'}:
                raise ValueError('Unsupported PhotoLine preview encoding.')
            preview.load()
            frame = ImageOps.exif_transpose(preview).copy()
    except (OSError, SyntaxError) as exc:
        raise ValueError('Cannot decode the embedded PhotoLine preview. Export the document from PhotoLine.') from exc
    kind = 'PhotoLine browser cache: first thumbnail only' if is_cache else 'PhotoLine document: embedded preview only'
    warning = f'{kind} ({frame.width} × {frame.height}); full-resolution layers are not imported.'
    return ImageDocument([frame], path, {'source_format': 'PLB' if is_cache else 'PLD',
                                        'preview_only': True, 'warnings': [warning]})


def _load_pdf(path: Path, dpi: float, all_pages: bool, password: str | None = None) -> ImageDocument:
    # PDFium is not thread-safe. A batch worker and thumbnail worker can render
    # simultaneously, so serialize every PDFium call in this process.
    with _PDFIUM_LOCK:
        return _load_pdf_locked(path, dpi, all_pages, password)


def _load_pdf_locked(path: Path, dpi: float, all_pages: bool, password: str | None) -> ImageDocument:
    if not _module('pypdfium2'):
        raise RuntimeError('PDF input requires pypdfium2. Run the installer again.')
    if not math.isfinite(float(dpi)) or not 18 <= float(dpi) <= 2400:
        raise ValueError('PDF rendering resolution must be between 18 and 2400 DPI.')
    import pypdfium2 as pdfium
    frames, sizes = [], []
    pdf = pdfium.PdfDocument(str(path), password=password)
    try:
        for i in range(len(pdf) if all_pages else min(len(pdf), 1)):
            page = pdf[i]
            try:
                sizes.append(page.get_size())
                bitmap = page.render(scale=float(dpi) / 72)
                try:
                    frames.append(bitmap.to_pil().copy())
                finally:
                    bitmap.close()
            finally:
                page.close()
        md = {'source_format': 'PDF', 'pdf_metadata': pdf.get_metadata_dict(),
              'pdf_page_sizes': sizes, 'frame_count': len(pdf)}
    finally:
        pdf.close()
    if not frames:
        raise ValueError('The PDF contains no pages.')
    return ImageDocument(frames, path, md, dpi=(float(dpi), float(dpi)))


def _load_svg(path: Path, target_size=(1024, 800), keep_aspect=False) -> ImageDocument:
    try:
        from PySide6.QtCore import QByteArray, QRectF, Qt
        from PySide6.QtGui import QImage, QPainter
        from PySide6.QtSvg import QSvgRenderer
    except ImportError as exc:
        raise RuntimeError('SVG input requires PySide6.QtSvg. Run the installer again.') from exc
    data = gzip.decompress(path.read_bytes()) if path.suffix.lower() == '.svgz' else path.read_bytes()
    renderer = QSvgRenderer(QByteArray(data))
    if not renderer.isValid():
        raise ValueError('The SVG document is invalid or unsupported.')
    size = renderer.defaultSize()
    if size.width() <= 0 or size.height() <= 0:
        size = renderer.viewBox().size()
    if size.width() <= 0 or size.height() <= 0:
        raise ValueError('The SVG has no usable dimensions or viewBox.')
    width, height = (int(value) for value in target_size)
    if min(width, height) < 1 or max(width, height) > 16384 or width * height > 100_000_000:
        raise ValueError('SVG raster size must be positive and no larger than 100 megapixels or 16384 pixels per side.')
    renderer.setAspectRatioMode(Qt.AspectRatioMode.KeepAspectRatio if keep_aspect
                               else Qt.AspectRatioMode.IgnoreAspectRatio)
    image = QImage(width, height, QImage.Format.Format_RGBA8888)
    if image.isNull():
        raise MemoryError('Cannot allocate the SVG raster at the requested size.')
    image.fill(0)
    painter = QPainter(image)
    try:
        renderer.render(painter, QRectF(0, 0, width, height))
    finally:
        painter.end()
    frame = Image.frombytes('RGBA', (image.width(), image.height()),
                            bytes(image.constBits()), 'raw', 'RGBA', image.bytesPerLine())
    return ImageDocument([frame], path, {'source_format': 'SVG', 'svg_raster_size': (width, height)})


def _flatten(im: Image.Image, background: str) -> Image.Image:
    if im.mode in {'RGBA', 'LA'} or im.mode == 'P' and 'transparency' in im.info:
        rgba = im.convert('RGBA')
        bg = Image.new('RGBA', im.size, ImageColor.getrgb(background))
        bg.alpha_composite(rgba)
        return bg.convert('RGB')
    return im.convert('RGB') if im.mode not in {'RGB', 'L', 'CMYK'} else im.copy()


def _has_alpha(im: Image.Image) -> bool:
    return 'A' in im.getbands() or 'transparency' in im.info


def _palette_bits(im: Image.Image) -> int:
    if im.mode != 'P':
        return 0
    colors = len(im.getpalette() or []) // 3
    return 4 if 0 < colors <= 16 and im.getextrema()[1] < 16 else 8


def _exif(md: dict, size: tuple[int, int], reset: bool, dpi: tuple[float, float]) -> bytes:
    if not md.get('exif'):
        return b''
    data = Image.Exif()
    data.load(md['exif'])
    if reset or 274 in data:
        data[274] = 1
    for tag, value in ((256, size[0]), (257, size[1])):
        if tag in data:
            data[tag] = value
    if 282 in data or 283 in data:
        data[282], data[283] = dpi
        data[296] = 2  # Resolution unit: inches.
    if 34665 in data:
        nested = data.get_ifd(34665)
        for tag, value in ((40962, size[0]), (40963, size[1])):
            if tag in nested:
                nested[tag] = value
    return data.tobytes()


def _bytes(value: Any) -> bytes:
    return value.encode('utf-8') if isinstance(value, str) else bytes(value)


def _metadata_options(doc: ImageDocument, opts: dict) -> dict:
    md, out = doc.metadata, {}
    if opts['keep_exif'] and md.get('exif'):
        out['exif'] = _exif(md, doc.frames[0].size, bool(opts['reset_orientation']), _dpi(doc.dpi))
    if opts['keep_icc'] and md.get('icc_profile'):
        out['icc_profile'] = md['icc_profile']
    if opts['keep_xmp'] and md.get('xmp'):
        out['xmp'] = _normalized_xmp(md['xmp'])
    if opts['keep_comment'] and md.get('comment'):
        out['comment'] = _bytes(md['comment'])
    return out


def _transparent(im: Image.Image, opts: dict) -> Image.Image:
    if not opts['custom_transparency']:
        return im.copy()
    mode = opts.get('transparency_mode', 'color')
    if mode == 'index':
        if im.mode != 'P':
            raise ValueError('Transparency by palette index requires an indexed-color image.')
        index = int(opts['transparency_index'])
        if not 0 <= index <= 255:
            raise ValueError('The transparent palette index must be between 0 and 255.')
        out = im.copy()
        out.info['transparency'] = index
        return out
    import numpy as np
    rgb = ImageColor.getrgb(opts['transparent_color'])[:3]
    data = np.array(im.convert('RGBA'))
    data[(data[:, :, :3] == rgb).all(axis=2), 3] = 0
    return Image.fromarray(data)


def save_document(doc: ImageDocument, path: str | Path, fmt: str, options: dict | None = None) -> dict:
    """Encode into a sibling temporary file, then replace the destination.

    Errors leave an existing destination intact. Returned warnings must be
    surfaced by the caller (e.g. the batch log), including lossy conversions.
    """
    if not doc.frames:
        raise ValueError('The document has no images.')
    fmt = normalize_format(fmt)
    if fmt not in available_formats():
        raise ValueError(f'{fmt} export is not available in this installation.')
    opts = default_format(fmt)
    opts.update(options or {})
    path = Path(path)
    if not path.parent.is_dir():
        raise FileNotFoundError(f'The output folder does not exist: {path.parent}')
    warnings = list(doc.metadata.get('warnings', []))
    frames = [im.copy() for im in doc.frames] if opts['all_pages'] else [doc.frames[0].copy()]
    if len(frames) > 1 and fmt not in _MULTIPAGE:
        warnings.append(f'{fmt} stores one image; only the first page/frame was saved.')
        frames = frames[:1]
    if opts['grayscale']:
        frames = [ImageOps.grayscale(im) if 'A' not in im.getbands()
                  else Image.merge('LA', (ImageOps.grayscale(im), im.getchannel('A'))) for im in frames]
    if fmt not in {'PNG', 'GIF', 'ICO'} and opts['custom_transparency']:
        warnings.append('Custom transparent color applies only to PNG, GIF and ICO.')
    fd, temp_name = tempfile.mkstemp(prefix=f'.{path.name}.', suffix='.tmp', dir=str(path.parent))
    os.close(fd)
    temporary = Path(temp_name)
    result = {'warnings': warnings, 'format': fmt, 'frames': len(frames)}
    try:
        _encode(frames, doc, temporary, fmt, opts, result)
        if not temporary.stat().st_size:
            raise OSError('The encoder produced an empty file.')
        with temporary.open('rb') as stream:
            os.fsync(stream.fileno())
        result['bytes'] = temporary.stat().st_size
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return result


def _encode(frames: list[Image.Image], doc: ImageDocument, path: Path,
            fmt: str, opts: dict, result: dict) -> None:
    md = _metadata_options(doc, opts)
    im = frames[0]
    # Image.info is copied by Pillow and can override opt-out metadata options.
    # Encoders receive metadata explicitly instead.
    for frame in frames:
        frame.info = {k: v for k, v in frame.info.items() if k == 'transparency'}
    common = {'dpi': _dpi(doc.dpi), **md}
    if opts['grayscale'] and common.get('icc_profile'):
        common.pop('icc_profile', None)
        md.pop('icc_profile', None)
        result['warnings'].append('The original color ICC profile was removed after conversion to grayscale.')
    elif common.get('icc_profile'):
        # Advanced transformations may have changed the image mode before this
        # encoder is called. An old RGB profile must not describe gray pixels.
        try:
            from PIL import ImageCms
            profile_space = ImageCms.ImageCmsProfile(BytesIO(common['icc_profile'])).profile.xcolor_space.strip()
            spaces = {'CMYK' if frame.mode == 'CMYK' else 'GRAY' if frame.mode in {'1', 'L', 'LA', 'I', 'I;16', 'I;16B', 'F'}
                      else 'RGB' for frame in frames}
            if any(space != profile_space for space in spaces):
                common.pop('icc_profile', None)
                md.pop('icc_profile', None)
                result['warnings'].append('The ICC profile was removed because its color space does not match the transformed pixels.')
        except (OSError, ValueError, ImageCms.PyCMSError):
            pass  # Preserve unknown profile bytes unless a mismatch is known.
    if any(frame.mode == 'CMYK' for frame in frames) and fmt not in {'JPEG', 'TIFF', 'RAW'}:
        # Many codecs accept only RGB. Keep the profile consistent with the
        # converted pixels instead of attaching a CMYK profile to RGB data.
        target_profile = None
        if common.get('icc_profile'):
            try:
                from PIL import ImageCms
                source_profile = ImageCms.ImageCmsProfile(BytesIO(common['icc_profile']))
                target_profile = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB'))
                frames = [ImageCms.profileToProfile(frame, source_profile, target_profile, outputMode='RGB')
                          if frame.mode == 'CMYK' else frame for frame in frames]
                common['icc_profile'] = md['icc_profile'] = target_profile.tobytes()
            except (OSError, ValueError, ImageCms.PyCMSError):
                target_profile = None
        if target_profile is None:
            frames = [frame.convert('RGB') if frame.mode == 'CMYK' else frame for frame in frames]
            common.pop('icc_profile', None)
            md.pop('icc_profile', None)
            result['warnings'].append('CMYK was converted to RGB without a usable color profile.')
        im = frames[0]
    if fmt == 'JPEG':
        _save_jpeg(im, doc, path, opts, common, result)
    elif fmt == 'PNG':
        _save_png(frames, doc, path, opts, common)
    elif fmt == 'GIF':
        _save_gif(frames, doc, path, opts, md, result)
    elif fmt == 'TIFF':
        _save_tiff(frames, doc, path, opts, common, result)
    elif fmt == 'PDF':
        _save_pdf(frames, doc, path, opts, result)
    elif fmt == 'JXL':
        import imagecodecs
        import numpy as np
        im = im.convert('RGBA' if _has_alpha(im) else 'RGB')
        path.write_bytes(imagecodecs.jpegxl_encode(np.asarray(im),
                         level=int(opts['quality']), lossless=bool(opts['lossless'])))
        if md:
            result['warnings'].append('JPEG XL export does not retain metadata.')
    elif fmt == 'JPEGLS':
        import imagecodecs
        import numpy as np
        im = _flatten(im, opts['background']).convert('L' if im.mode == 'L' else 'RGB')
        path.write_bytes(imagecodecs.jpegls_encode(np.asarray(im), level=0))
        if md:
            result['warnings'].append('JPEG-LS export does not retain metadata.')
    elif fmt == 'RAW':
        mode = opts['raw_mode']
        if mode not in {'RGB', 'RGBA', 'L', 'CMYK'}:
            raise ValueError('RAW mode must be RGB, RGBA, L, or CMYK.')
        path.write_bytes(im.convert(mode).tobytes())
        result['warnings'].append(f'Headerless RAW: {im.width} × {im.height}, {mode}, 8-bit channels, row-major bytes.')
    elif fmt in {'PBM', 'PGM', 'PPM'}:
        mode = {'PBM': '1', 'PGM': 'L', 'PPM': 'RGB'}[fmt]
        im = _flatten(im, opts['background']).convert(mode)
        if opts.get('pnm_ascii'):
            _save_ascii_pnm(im, path, fmt)
        else:
            im.save(path, format='PPM')
    elif fmt == 'ICO':
        im = _transparent(im, opts)
        sizes = sorted({int(s) for s in opts['ico_sizes'] if 1 <= int(s) <= 256})
        if not sizes:
            raise ValueError('Select at least one icon size from 1 to 256 pixels.')
        base = ImageOps.pad(im.convert('RGBA'), (max(sizes), max(sizes)),
                            method=Image.Resampling.LANCZOS, color=(0, 0, 0, 0))
        base.save(path, format='ICO', sizes=[(s, s) for s in sizes])
    elif fmt in {'WEBP', 'AVIF', 'HEIF'}:
        params = {k: v for k, v in common.items() if k in {'exif', 'icc_profile', 'xmp'}}
        params.update(quality=int(opts['quality']), lossless=bool(opts['lossless']))
        if fmt == 'HEIF' and opts['lossless']:
            params.update(quality=-1, chroma=444, matrix_coefficients=0)
        if fmt == 'WEBP':
            params['method'] = 6
        if fmt == 'AVIF' and opts['lossless']:
            # Pillow's AVIF writer accepts quality, not a lossless switch.
            raise ValueError('Lossless AVIF is not exposed by this encoder. Use PNG, TIFF, WebP, HEIF or JPEG XL.')
        frames = [frame.convert('RGBA' if 'A' in frame.getbands() or 'transparency' in frame.info else 'RGB')
                  for frame in frames]
        if len(frames) > 1:
            params.update(save_all=True, append_images=frames[1:], duration=_durations(doc, len(frames)), loop=doc.loop)
            if fmt == 'HEIF':
                result['warnings'].append('HEIF export stores an image collection; animation timing is not retained.')
        frames[0].save(path, format=fmt, **params)
    elif fmt == 'JPEG2000':
        im = im.convert('RGBA' if _has_alpha(im) else 'RGB')
        params = {'irreversible': not opts['lossless']}
        if not opts['lossless']:
            params.update(quality_mode='rates', quality_layers=[max(1.0, 100.0 / max(1, float(opts['quality'])))])
        im.save(path, format=fmt, **params)
    elif fmt in {'BMP', 'PCX', 'EPS'}:
        if _has_alpha(im) or im.mode not in {'1', 'L', 'P', 'RGB'}:
            im = _flatten(im, opts['background'])
        if fmt == 'BMP' and _palette_bits(im) == 4:
            _save_bmp4(im, path, doc.dpi)
        else:
            if fmt == 'EPS' and im.mode not in {'L', 'RGB', 'CMYK'}:
                im = im.convert('L' if im.mode == '1' else 'RGB')
            if fmt == 'PCX' and im.mode not in {'RGB', 'L', '1', 'P'}:
                im = im.convert('RGB')
            im.save(path, format=fmt, dpi=doc.dpi)
            if fmt == 'EPS':
                with path.open('ab') as stream:
                    stream.write(b'%%EOF\n')
    elif fmt in {'QOI', 'DDS', 'TGA'}:
        im.convert('RGBA' if _has_alpha(im) else 'RGB').save(path, format=fmt)
    else:
        raise ValueError(f'No encoder has been implemented for {fmt}.')
    if md and fmt in {'BMP', 'ICO', 'JPEG2000', 'PCX', 'PBM', 'PGM', 'PPM', 'QOI', 'TGA', 'DDS', 'EPS', 'RAW'}:
        result['warnings'].append(f'{fmt} export does not retain EXIF, IPTC, XMP, comments or ICC metadata.')


def _durations(doc: ImageDocument, count: int) -> list[int]:
    return [max(0, int(doc.durations[i])) if i < len(doc.durations) else 100 for i in range(count)]


def _save_jpeg(im, doc, path, opts, common, result):
    im = _flatten(im, opts['background'])
    if opts['grayscale']:
        im = im.convert('L')
    params = dict(common, quality=int(opts['quality']), progressive=bool(opts['progressive']),
                  optimize=True, subsampling=0 if opts['no_subsampling'] else 2,
                  comment=common.get('comment', b''))
    if opts['keep_iptc']:
        segments = doc.metadata.get('jpeg_iptc_segments', [])
        params['extra'] = b''.join(b'\xff\xed' + struct.pack('>H', len(s) + 2) + s for s in segments)
    if opts['original_quality']:
        qtables = doc.metadata.get('jpeg_qtables')
        if not qtables:
            raise ValueError('Original JPEG quality is available only for an image loaded from a JPEG.')
        params.pop('quality')
        params['qtables'] = deepcopy(qtables)
        if not opts['no_subsampling']:
            params['subsampling'] = doc.metadata.get('jpeg_subsampling', 2)
    target = float(opts['target_kb'])
    if target > 0:
        if opts['original_quality']:
            raise ValueError('Target file size and original JPEG quality cannot be selected together.')
        target_bytes = int(target * 1024)
        # Bound the work to eight encodes, keeping a tested result that actually
        # fits. JPEG file size can have small nonmonotonic changes with quality.
        best, low, high = None, 0, 100
        while low <= high:
            quality = (low + high) // 2
            stream = BytesIO()
            im.save(stream, format='JPEG', **dict(params, quality=quality))
            data = stream.getvalue()
            if len(data) <= target_bytes:
                best = data
                result['quality'] = quality
                low = quality + 1
            else:
                high = quality - 1
        if best is None:
            raise ValueError(f'The image cannot fit within {target:g} KB at any JPEG quality. Increase the target or resize it.')
        path.write_bytes(best)
    else:
        im.save(path, format='JPEG', **params)


def _save_png(frames, doc, path, opts, common):
    frames = [_transparent(im, opts) for im in frames]
    params = {k: v for k, v in common.items() if k in {'dpi', 'exif', 'icc_profile'}}
    params['compress_level'] = int(opts['compression'])
    if not 0 <= params['compress_level'] <= 9:
        raise ValueError('PNG compression must be between 0 and 9.')
    text = PngImagePlugin.PngInfo()
    if opts['keep_comment']:
        for key, value in doc.metadata.get('png_text', {}).items():
            text.add_itxt(key, str(value))
        if common.get('comment'):
            text.add_itxt('Comment', _bytes(common['comment']).decode('utf-8', 'replace'))
    if common.get('xmp'):
        text.add_itxt('XML:com.adobe.xmp', common['xmp'].decode('utf-8', 'replace'))
    params['pnginfo'] = text
    if opts['custom_transparency'] and not opts.get('transparency_alpha', True) and opts.get('transparency_mode') != 'index':
        rgb = ImageColor.getrgb(opts['transparent_color'])[:3]
        frames = [im.convert('RGB') for im in frames]
        params['transparency'] = rgb
    if len(frames) > 1:
        params.update(save_all=True, append_images=frames[1:], duration=_durations(doc, len(frames)),
                      loop=doc.loop, disposal=0, blend=0)
    frames[0].save(path, format='PNG', **params)
    if opts['optimize_png']:
        binary = find_executable('optipng')
        if not binary:
            raise RuntimeError('OptiPNG is not installed. Disable "Run OptiPNG" or install it with: brew install optipng')
        level = int(opts['optipng_level'])
        if not 0 <= level <= 7:
            raise ValueError('OptiPNG optimization level must be between 0 and 7.')
        completed = subprocess.run([binary, f'-o{level}', '-quiet', str(path)], capture_output=True, text=True, timeout=300)
        if completed.returncode:
            raise RuntimeError(f'OptiPNG failed: {completed.stderr.strip() or completed.stdout.strip()}')


def _gif_palette(im: Image.Image, opts: dict) -> tuple[Image.Image, bool]:
    im = _transparent(im, opts)
    if im.mode == 'P' and isinstance(im.info.get('transparency'), int):
        return im, True
    rgba = im.convert('RGBA')
    alpha = rgba.getchannel('A')
    transparent = alpha.getextrema()[0] < 128
    pal = rgba.convert('RGB').quantize(colors=255 if transparent else 256,
                    method=Image.Quantize.MEDIANCUT,
                    dither=Image.Dither.FLOYDSTEINBERG if opts.get('dither', True) else Image.Dither.NONE)
    if transparent:
        pal.paste(255, mask=alpha.point(lambda value: 255 if value < 128 else 0))
        palette = pal.getpalette() or []
        palette = (palette + [0] * 768)[:768]
        pal.putpalette(palette)
        pal.info['transparency'] = 255
    return pal, transparent


def _save_gif(frames, doc, path, opts, md, result):
    palettes = [_gif_palette(im, opts)[0] for im in frames]
    params = {'interlace': bool(opts['gif_interlace']), 'optimize': False}
    if md.get('comment'):
        params['comment'] = md['comment']
    if len(palettes) > 1:
        if any(im.size != palettes[0].size for im in palettes):
            raise ValueError('All frames of an animated GIF must have the same dimensions.')
        params.update(save_all=True, append_images=palettes[1:], duration=_durations(doc, len(palettes)),
                      loop=doc.loop, disposal=2)
    if 'transparency' in palettes[0].info:
        params['transparency'] = palettes[0].info['transparency']
    palettes[0].save(path, format='GIF', **params)
    if any('A' in im.getbands() and len(im.getchannel('A').getcolors(maxcolors=257) or []) > 2 for im in frames):
        result['warnings'].append('GIF supports binary transparency; partial alpha was thresholded at 128.')


def _save_tiff(frames, doc, path, opts, common, result):
    aliases = {'none': 'raw', 'None': 'raw', 'LZW': 'tiff_lzw', 'ZIP': 'tiff_adobe_deflate',
               'Packbits': 'packbits', 'JPEG': 'jpeg', 'Huffman RLE': 'tiff_ccitt',
               'Fax3': 'group3', 'Fax4': 'group4', 'zip': 'tiff_adobe_deflate'}
    compression = aliases.get(opts['tiff_compression'], opts['tiff_compression']) or 'raw'
    if compression not in {'raw', 'tiff_lzw', 'packbits', 'jpeg', 'tiff_adobe_deflate', 'tiff_ccitt', 'group3', 'group4'}:
        raise ValueError(f'Unsupported TIFF compression: {compression}')
    frames = [frame.convert('RGBA') if frame.mode == 'P' and _has_alpha(frame) else frame for frame in frames]
    if compression in {'tiff_ccitt', 'group3', 'group4'}:
        method = opts['tiff_bilevel_method']
        if method not in {'adaptive', 'compact', 'floyd_steinberg', 'threshold'}:
            raise ValueError(f'Unknown black-and-white conversion: {method}')
        threshold = int(opts['tiff_threshold'])
        if not 0 <= threshold <= 255:
            raise ValueError('Black-and-white threshold must be between 0 and 255.')
        contrast = float(opts['tiff_bilevel_contrast'])
        if not math.isfinite(contrast) or not 1 <= contrast <= 6:
            raise ValueError('Black-and-white contrast must be between 1 and 6.')
        if any(im.mode != '1' for im in frames):
            description = {'adaptive': 'adaptive two-color dithering',
                           'compact': f'high-contrast dithering (contrast {contrast:g})',
                           'floyd_steinberg': 'Floyd–Steinberg dithering',
                           'threshold': f'threshold {threshold}'}[method]
            result['warnings'].append(f'Converted to 1-bit black and white using {description}.')
        converted = []
        for im in frames:
            if im.mode == '1':
                converted.append(im)
                continue
            if method == 'adaptive':
                from .bilevel import adaptive_bilevel
                # A vector canvas has no stored RGB behind transparent areas;
                # rasterize that canvas on the chosen output background.
                composite = (opts['tiff_adaptive_alpha_background']
                             or doc.metadata.get('source_format') == 'SVG')
                rgb = (_flatten(im, opts['background']) if composite
                       else im.convert('RGB'))
                converted.append(adaptive_bilevel(rgb))
                continue
            gray = _flatten(im, opts['background']).convert('L')
            if method == 'threshold':
                converted.append(gray.point([0 if v < threshold else 255 for v in range(256)], mode='1'))
            else:
                if method == 'compact':
                    # Normalize the available tone range before boosting contrast.
                    # This avoids filling tinted paper and flat color fields with
                    # fax-unfriendly dots. It changes tones, never pixel dimensions.
                    gray = ImageOps.autocontrast(gray).point([
                        max(0, min(255, round(128 + (value - 128) * contrast)))
                        for value in range(256)])
                converted.append(gray.convert('1', dither=Image.Dither.FLOYDSTEINBERG))
        frames = converted
        if common.get('icc_profile'):
            common = dict(common)
            common.pop('icc_profile')
            result['warnings'].append('The color ICC profile was removed after conversion to black and white.')
    elif compression == 'jpeg':
        frames = [_flatten(im, opts['background']) for im in frames]
    elif opts.get('tiff_gray_palette'):
        converted = []
        for im in frames:
            if im.mode == 'L':
                pal = Image.frombytes('P', im.size, im.tobytes())
                pal.putpalette([v for value in range(256) for v in (value, value, value)])
                converted.append(pal)
            else:
                converted.append(im)
        frames = converted
    if any(_palette_bits(frame) == 4 for frame in frames):
        _save_packed_tiff(frames, doc, path, compression, common, result)
        return
    params = {k: v for k, v in common.items() if k in {'dpi', 'icc_profile'}}
    params['compression'] = compression
    fax = compression in {'tiff_ccitt', 'group3', 'group4'}
    if fax:
        # One strip per page avoids resetting the fax codec between strips.
        params['strip_size'] = max(((frame.width + 7) // 8) * frame.height for frame in frames)
    if compression == 'jpeg':
        params['quality'] = int(opts['quality'])
    tiffinfo = Image.Exif()
    if common.get('exif'):
        tiffinfo.load(common['exif'])
        # TIFF getexif() also returns storage/layout tags. Reusing those after
        # resizing, changing codec, or on a different-size next page corrupts
        # the directory. Let the encoder describe its actual pixel storage.
        for tag in (256, 257, 258, 259, 262, 266, 273, 277, 278, 279, 284,
                    292, 293, 317, 320, 322, 323, 324, 325, 330, 338, 339, 347,
                    512, 513, 514, 515, 517, 518, 519, 520, 521, 529, 530, 531, 532,
                    270, 700, 33723, 34377, 34675):
            if tag in tiffinfo:
                del tiffinfo[tag]
    if common.get('xmp'):
        tiffinfo[700] = common['xmp']
    if common.get('comment'):
        tiffinfo[270] = common['comment'].decode('utf-8', 'replace')
    if opts['keep_iptc'] and doc.metadata.get('tiff_iptc'):
        tiffinfo[33723] = doc.metadata['tiff_iptc']
    if fax:
        # Pillow inverts the storage bits while retaining the decoded pixels.
        # Min-is-white matches the conventional fax representation.
        tiffinfo[262] = 0
    # libtiff cannot reliably encode nested EXIF/GPS dictionaries in Classic
    # TIFF. Keep their contents for a separate metadata pass after compression.
    if tiffinfo:
        params['tiffinfo'] = {tag: tiffinfo[tag] for tag in tiffinfo
                              if tag not in (34665, 34853)}
    if len(frames) > 1:
        params.update(save_all=True, append_images=frames[1:])
    frames[0].save(path, format='TIFF', **params)
    result.update(compression=compression, pixel_size=frames[0].size, color_mode=frames[0].mode)
    if 34665 in tiffinfo or 34853 in tiffinfo:
        from .tiff_metadata import append_nested_exif
        append_nested_exif(path, tiffinfo, [frame.size for frame in frames])


def _save_packed_tiff(frames, doc, path, compression, common, result):
    """Pillow writes every P-mode TIFF at 8 bits; use libtiff's packed path."""
    if not _module('imagecodecs'):
        raise RuntimeError('4-bit TIFF export requires imagecodecs. Run the installer again.')
    import imagecodecs
    import numpy as np
    encoded = None
    for frame in frames:
        options = {'compression': {'raw': 'none', 'tiff_lzw': 'lzw', 'packbits': 'packbits',
                                    'tiff_adobe_deflate': 'deflate'}[compression],
                   'resolution': _dpi(doc.dpi), 'resolutionunit': 'inch',
                   'software': 'LumaView'}
        if frame.mode == 'P':
            palette = ((frame.getpalette() or []) + [0] * 768)[:768]
            options.update(photometric='palette', bitspersample=_palette_bits(frame),
                           colormap=np.array(palette, dtype=np.uint16).reshape(256, 3).T * 257)
        elif frame.mode in {'RGB', 'RGBA'}:
            options['photometric'] = 'rgb'
        elif frame.mode == 'CMYK':
            options['photometric'] = 'separated'
        else:
            options['photometric'] = 'minisblack'
        if frame.mode in {'RGBA', 'LA'}:
            options['extrasample'] = 2  # unassociated alpha
        if common.get('icc_profile'):
            options['iccprofile'] = common['icc_profile']
        if common.get('comment'):
            options['description'] = common['comment'].decode('utf-8', 'replace')
        if encoded is not None:
            options['appendto'] = encoded
        encoded = imagecodecs.tiff_encode(np.asarray(frame), **options)
    path.write_bytes(encoded)
    if common.get('exif') or common.get('xmp') or doc.metadata.get('tiff_iptc'):
        result['warnings'].append('4-bit TIFF export retains DPI, ICC and comments, but not EXIF, XMP or IPTC metadata.')


def _save_bmp4(im: Image.Image, path: Path, dpi) -> None:
    """Write standard uncompressed BI_RGB with real four-bit packed pixels."""
    width, height = im.size
    stride = ((width * 4 + 31) // 32) * 4
    colors = ((im.getpalette() or []) + [0] * 48)[:48]
    palette = b''.join(bytes((colors[i + 2], colors[i + 1], colors[i], 0)) for i in range(0, 48, 3))
    offset = 14 + 40 + len(palette)
    data_size = stride * height
    xdpi, ydpi = _dpi(dpi)
    with path.open('wb') as output:
        output.write(struct.pack('<2sIHHI', b'BM', offset + data_size, 0, 0, offset))
        output.write(struct.pack('<IiiHHIIiiII', 40, width, height, 1, 4, 0, data_size,
                                 round(xdpi / .0254), round(ydpi / .0254), 16, 16))
        output.write(palette)
        pixels = im.tobytes()
        for y in range(height - 1, -1, -1):
            row = pixels[y * width:(y + 1) * width]
            packed = bytearray(stride)
            for x, value in enumerate(row):
                packed[x // 2] |= value << (4 if x % 2 == 0 else 0)
            output.write(packed)


def _save_pdf(frames, doc, path, opts, result):
    from reportlab.pdfgen import canvas
    from reportlab.lib.utils import ImageReader
    from reportlab.lib import pagesizes
    from reportlab.lib.pdfencrypt import StandardEncryption
    compression = str(opts['pdf_compression']).lower()
    if compression not in {'jpeg', 'zip', 'flate', 'lossless', 'none'}:
        raise ValueError('PDF compression must be JPEG or lossless ZIP/Flate.')
    encrypt = None
    password = opts.get('pdf_password', '')
    owner = opts.get('pdf_owner_password', '')
    if password or owner:
        encrypt = StandardEncryption(userPassword=password, ownerPassword=owner or None,
                    canPrint=bool(opts.get('pdf_allow_print', True)),
                    canModify=bool(opts.get('pdf_allow_modify', True)),
                    canCopy=bool(opts.get('pdf_allow_copy', True)),
                    canAnnotate=bool(opts.get('pdf_allow_annotate', True)), strength=128)
    output = canvas.Canvas(str(path), pageCompression=compression != 'none', encrypt=encrypt)
    output.setTitle(str(opts.get('pdf_title', '')))
    output.setAuthor(str(opts.get('pdf_author', '')))
    output.setCreator('LumaView')
    size_name = str(opts['pdf_page_size']).lower()
    paper = {'a4': pagesizes.A4, 'a3': pagesizes.A3, 'a5': pagesizes.A5,
             'letter': pagesizes.letter, 'legal': pagesizes.legal}
    if size_name != 'image' and size_name not in paper:
        raise ValueError(f'Unknown PDF page size: {size_name}')
    # PDF export resolution sets physical dimensions. It does not add pixels.
    dpi = float(opts['pdf_dpi'])
    if not math.isfinite(dpi) or dpi <= 0:
        raise ValueError('PDF resolution must be greater than zero.')
    for im in frames:
        physical = (im.width * 72 / dpi, im.height * 72 / dpi)
        size = physical if size_name == 'image' else paper[size_name]
        if opts.get('pdf_landscape'):
            size = pagesizes.landscape(size)
        output.setPageSize(size)
        if size_name == 'image':
            draw_w, draw_h = physical
        elif opts['pdf_fit']:
            scale = min(size[0] / physical[0], size[1] / physical[1])
            draw_w, draw_h = physical[0] * scale, physical[1] * scale
        else:
            draw_w, draw_h = physical
        x, y = (size[0] - draw_w) / 2, (size[1] - draw_h) / 2
        if compression == 'jpeg':
            stream = BytesIO()
            _flatten(im, opts['background']).save(stream, format='JPEG', quality=int(opts['pdf_quality']), optimize=True)
            stream.seek(0)
            reader = ImageReader(stream)
        else:
            reader = ImageReader(im.convert('RGBA' if 'A' in im.getbands() or 'transparency' in im.info else 'RGB'))
        output.drawImage(reader, x, y, width=draw_w, height=draw_h, mask='auto')
        output.showPage()
    output.save()
    if compression == 'none':
        result['warnings'].append('PDF page streams are uncompressed; embedded raster data uses lossless Flate compression.')
    result['warnings'].append('PDF export contains raster images; original searchable text, links and vector objects are not retained.')


def _save_ascii_pnm(im: Image.Image, path: Path, fmt: str) -> None:
    magic = {'PBM': 'P1', 'PGM': 'P2', 'PPM': 'P3'}[fmt]
    with path.open('w', encoding='ascii', newline='\n') as stream:
        stream.write(f'{magic}\n{im.width} {im.height}\n')
        if fmt != 'PBM':
            stream.write('255\n')
        for y in range(im.height):
            pixels = [im.getpixel((x, y)) for x in range(im.width)]
            if fmt == 'PPM':
                values = [str(v) for pixel in pixels for v in pixel]
            elif fmt == 'PBM':
                values = ['0' if value else '1' for value in pixels]
            else:
                values = [str(v) for v in pixels]
            stream.write(' '.join(values) + '\n')
