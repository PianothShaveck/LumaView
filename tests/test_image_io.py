from copy import deepcopy
from pathlib import Path
import struct

import numpy as np
import pytest
from PIL import Image, ImageChops, JpegImagePlugin

from lumaview.image_io import available_formats, load_document, save_document, supported_input_extensions
from lumaview.model import ImageDocument


def sample(mode='RGB', size=(64, 48)):
    y, x = np.indices((size[1], size[0]))
    arr = np.stack((x * 3 % 256, y * 5 % 256, (x + y) * 2 % 256), axis=-1).astype('uint8')
    return Image.fromarray(arr).convert(mode)


def equal(a, b):
    assert a.size == b.size
    assert np.array_equal(np.array(a.convert('RGBA')), np.array(b.convert('RGBA')))


@pytest.mark.parametrize('fmt', ['PNG', 'TIFF', 'BMP', 'PPM', 'QOI', 'TGA', 'DDS'])
def test_lossless_roundtrip(tmp_path, fmt):
    original = sample()
    path = tmp_path / ('image.' + fmt.lower())
    result = save_document(ImageDocument([original], dpi=(300, 300)), path, fmt, {})
    decoded = load_document(path)
    equal(decoded.frames[0], original)
    assert result['bytes'] > 0
    if fmt in {'PNG', 'TIFF'}:
        assert decoded.dpi[0] == pytest.approx(300, abs=0.1)


@pytest.mark.parametrize('compression', ['raw', 'tiff_lzw', 'packbits', 'jpeg', 'tiff_adobe_deflate',
                                        'tiff_ccitt', 'group3', 'group4'])
def test_all_tiff_compressions_multipage(tmp_path, compression):
    bilevel = compression in {'tiff_ccitt', 'group3', 'group4'}
    frames = [sample().convert('1') if bilevel else sample(), Image.new('1' if bilevel else 'RGB', (30, 50), 'white')]
    path = tmp_path / 'multi.tif'
    save_document(ImageDocument(frames), path, 'TIFF', {'tiff_compression': compression})
    doc = load_document(path)
    assert len(doc.frames) == 2
    assert [im.size for im in doc.frames] == [(64, 48), (30, 50)]
    if compression != 'jpeg':
        equal(doc.frames[0], frames[0])
        equal(doc.frames[1], frames[1])


def test_tiff_gray_palette(tmp_path):
    path = tmp_path / 'gray.tif'
    original = sample('L')
    save_document(ImageDocument([original]), path, 'TIFF', {'tiff_gray_palette': True})
    doc = load_document(path)
    assert doc.frames[0].mode == 'P'
    equal(doc.frames[0], original)
    save_document(ImageDocument([original]), path, 'TIFF', {'tiff_gray_palette': False})
    assert load_document(path).frames[0].mode == 'L'


def test_jpeg_metadata_quality_and_orientation(tmp_path):
    exif = Image.Exif()
    exif[274] = 6
    exif[315] = 'LumaView test'
    exif[256] = 64
    exif[257] = 48
    xmp = b'<x:xmpmeta xmlns:x="adobe:ns:meta/" xmlns:tiff="http://ns.adobe.com/tiff/1.0/" tiff:Orientation="6"/>'
    iptc = b'Photoshop 3.0\0' + b'8BIM\x04\x04\0\0\0\0\0\x08' + b'\x1c\x02\x05\0\x03abc'
    app13 = b'\xff\xed' + struct.pack('>H', len(iptc) + 2) + iptc
    source = tmp_path / 'source.jpg'
    sample().save(source, quality=83, exif=exif, xmp=xmp, comment=b'Photo comment', extra=app13,
                  icc_profile=b'profile-test', subsampling=0)
    original_bytes = source.read_bytes()
    doc = load_document(source)
    assert doc.frames[0].size == (48, 64)
    metadata_before = deepcopy(doc.metadata)
    target = tmp_path / 'target.jpg'
    save_document(doc, target, 'JPEG', {'original_quality': True})
    with Image.open(target) as output:
        assert output.getexif()[274] == 1
        assert output.getexif()[315] == 'LumaView test'
        assert output.getexif()[256] == 48
        assert output.getexif()[257] == 64
        assert output.info['comment'] == b'Photo comment'
        assert output.info['icc_profile'] == b'profile-test'
        assert b'tiff:Orientation="1"' in output.info['xmp']
        assert output.quantization == doc.metadata['jpeg_qtables']
        assert JpegImagePlugin.get_sampling(output) == 0
        assert any(data == iptc for marker, data in output.applist if marker == 'APP13')
    assert doc.metadata == metadata_before
    assert source.read_bytes() == original_bytes
    save_document(doc, target, 'JPEG', {k: False for k in ('keep_exif', 'keep_iptc', 'keep_xmp', 'keep_comment', 'keep_icc')})
    with Image.open(target) as output:
        assert not output.getexif()
        assert not output.info.get('comment')
        assert not output.info.get('xmp')
        assert not output.info.get('icc_profile')
        assert not any(marker == 'APP13' for marker, data in output.applist)


def test_jpeg_target_and_atomic_failure(tmp_path):
    pixels = np.random.default_rng(14).integers(0, 256, (128, 128, 3), dtype='uint8')
    doc = ImageDocument([Image.fromarray(pixels)])
    path = tmp_path / 'target.jpg'
    result = save_document(doc, path, 'JPEG', {'target_kb': 5})
    assert path.stat().st_size <= 5 * 1024
    assert 0 <= result['quality'] <= 100
    previous = path.read_bytes()
    with pytest.raises(ValueError, match='cannot fit'):
        save_document(doc, path, 'JPEG', {'target_kb': .01})
    assert path.read_bytes() == previous
    assert not list(tmp_path.glob('.*.tmp'))


@pytest.mark.parametrize('alpha', [True, False])
def test_png_custom_transparency(tmp_path, alpha):
    im = Image.new('RGB', (10, 10), 'white')
    im.putpixel((1, 1), (255, 0, 0))
    path = tmp_path / 'transparent.png'
    save_document(ImageDocument([im]), path, 'PNG', {'custom_transparency': True,
                            'transparent_color': '#ffffff', 'transparency_alpha': alpha})
    result = load_document(path).frames[0].convert('RGBA')
    assert result.getpixel((0, 0))[3] == 0
    assert result.getpixel((1, 1)) == (255, 0, 0, 255)


def test_png_animation_and_gif_roundtrip(tmp_path):
    frames = [Image.new('RGBA', (64, 48), color) for color in ('red', 'blue')]
    frames[0].putpixel((0, 0), (0, 0, 0, 0))
    doc = ImageDocument(frames, durations=[120, 250], loop=2)
    for fmt in ('PNG', 'GIF', 'WEBP'):
        path = tmp_path / ('anim.' + fmt.lower())
        save_document(doc, path, fmt, {'lossless': True})
        loaded = load_document(path)
        assert len(loaded.frames) == 2
        assert loaded.durations == [120, 250]
        assert loaded.loop == 2
        assert loaded.frames[0].convert('RGBA').getpixel((0, 0))[3] == 0


def test_gif_palette_index(tmp_path):
    im = Image.new('P', (20, 20), 2)
    im.putpalette([value for i in range(256) for value in (i, i, i)])
    im.putpixel((0, 0), 1)
    path = tmp_path / 'palette.gif'
    save_document(ImageDocument([im]), path, 'GIF', {'custom_transparency': True,
                  'transparency_mode': 'index', 'transparency_index': 2, 'gif_interlace': True})
    loaded = load_document(path).frames[0].convert('RGBA')
    assert loaded.getpixel((0, 0))[3] == 255
    assert loaded.getpixel((1, 1))[3] == 0


@pytest.mark.parametrize('fmt', ['PBM', 'PGM', 'PPM'])
def test_ascii_pnm(tmp_path, fmt):
    path = tmp_path / ('image.' + fmt.lower())
    save_document(ImageDocument([sample()]), path, fmt, {'pnm_ascii': True})
    assert path.read_bytes().startswith({'PBM': b'P1', 'PGM': b'P2', 'PPM': b'P3'}[fmt])
    assert load_document(path).frames[0].size == (64, 48)


@pytest.mark.parametrize('compression', ['jpeg', 'lossless'])
def test_pdf_pages_and_password(tmp_path, compression):
    pytest.importorskip('pypdfium2')
    frames = [sample(), sample(size=(120, 160))]
    path = tmp_path / 'pages.pdf'
    save_document(ImageDocument(frames), path, 'PDF', {'pdf_compression': compression,
                  'pdf_password': 'secret', 'pdf_dpi': 144, 'pdf_title': 'Two images'})
    with pytest.raises(Exception):
        load_document(path)
    loaded = load_document(path, pdf_dpi=144, password='secret')
    assert len(loaded.frames) == 2
    assert [im.size for im in loaded.frames] == [(64, 48), (120, 160)]
    assert loaded.metadata['pdf_metadata']['Title'] == 'Two images'
    assert len(load_document(path, all_pages=False, password='secret').frames) == 1


def test_pdf_a4_fit(tmp_path):
    pdfium = pytest.importorskip('pypdfium2')
    path = tmp_path / 'a4.pdf'
    save_document(ImageDocument([sample()]), path, 'PDF', {'pdf_page_size': 'A4', 'pdf_landscape': True})
    pdf = pdfium.PdfDocument(str(path))
    try:
        page = pdf[0]
        try:
            assert page.get_width() == pytest.approx(841.89, abs=.1)
            assert page.get_height() == pytest.approx(595.28, abs=.1)
        finally:
            page.close()
    finally:
        pdf.close()


@pytest.mark.parametrize('fmt,ext', [('JXL', 'jxl'), ('JPEG2000', 'jp2'), ('HEIF', 'heic'), ('JPEGLS', 'jls')])
def test_optional_lossless_encoders(tmp_path, fmt, ext):
    if fmt not in available_formats():
        pytest.skip(f'{fmt} unavailable')
    path = tmp_path / ('image.' + ext)
    im = sample()
    save_document(ImageDocument([im]), path, fmt, {'lossless': True})
    equal(load_document(path).frames[0], im)


def test_no_silent_missing_optipng(tmp_path, monkeypatch):
    monkeypatch.setattr('lumaview.image_io.shutil.which', lambda _: None)
    path = tmp_path / 'image.png'
    with pytest.raises(RuntimeError, match='OptiPNG is not installed'):
        save_document(ImageDocument([sample()]), path, 'PNG', {'optimize_png': True})
    assert not path.exists()


def test_available_export_formats_are_real(tmp_path):
    for fmt in available_formats():
        path = tmp_path / ('test.' + fmt.lower())
        result = save_document(ImageDocument([sample()]), path, fmt, {})
        assert result['bytes'] == path.stat().st_size
    assert '.png' in supported_input_extensions()


def test_source_frame_and_metadata_unchanged(tmp_path):
    im = sample('RGBA')
    im.info['comment'] = b'original'
    doc = ImageDocument([im], metadata={'comment': b'original'})
    before = doc.clone()
    save_document(doc, tmp_path / 'a.jpg', 'JPEG', {'grayscale': True, 'keep_comment': False})
    equal(doc.frames[0], before.frames[0])
    assert doc.frames[0].info == before.frames[0].info
    assert doc.metadata == before.metadata


def test_cmyk_conversion_drops_incompatible_profile(tmp_path):
    doc = ImageDocument([sample('CMYK')], metadata={'icc_profile': b'invalid-cmyk-profile'})
    path = tmp_path / 'rgb.png'
    result = save_document(doc, path, 'PNG', {})
    loaded = load_document(path)
    assert loaded.frames[0].mode == 'RGB'
    assert not loaded.metadata.get('icc_profile')
    assert any('CMYK' in warning for warning in result['warnings'])


def test_svg_rasterization(tmp_path):
    pytest.importorskip('PySide6.QtSvg')
    path = tmp_path / 'vector.svg'
    path.write_text('<svg width="20" height="30" xmlns="http://www.w3.org/2000/svg">'
                    '<rect width="20" height="30" fill="red"/></svg>')
    frame = load_document(path, svg_size=(20, 30)).frames[0]
    assert frame.size == (20, 30)
    assert load_document(path).frames[0].size == (1024, 800)
    assert frame.getpixel((5, 5)) == (255, 0, 0, 255)
