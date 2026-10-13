"""Regression for compressed TIFFs with nested EXIF/GPS directories."""
from copy import deepcopy
from io import BytesIO

import numpy as np
import pytest
from PIL import Image

from lumaview.image_io import load_document, save_document
from lumaview.model import ImageDocument


@pytest.mark.parametrize('compression', ['raw', 'tiff_lzw', 'packbits', 'jpeg',
                                        'tiff_adobe_deflate', 'tiff_ccitt', 'group3', 'group4'])
def test_nested_exif_survives_compressed_multipage_tiff(tmp_path, compression):
    exif = Image.Exif()
    exif[315] = 'Metadata regression'
    exif[274] = 6
    exif[34665] = {36867: '2026:10:13 12:34:56', 40962: 999, 40963: 999,
                   37510: b'ASCII\0\0\0Screenshot', 40965: {1: 'R98'}}
    exif[34853] = {1: 'N', 2: (40.0, 30.0, 0.0), 3: 'E', 4: (12.0, 0.0, 0.0)}
    frames = [Image.new('RGB', (100, 70), 'white'), Image.new('RGB', (61, 37), 'black')]
    doc = ImageDocument(frames, metadata={'exif': exif.tobytes()}, dpi=(300, 300))
    metadata_before = deepcopy(doc.metadata)
    path = tmp_path / 'result.tif'
    result = save_document(doc, path, 'TIFF', {'tiff_compression': compression})
    assert not any('metadata' in warning.lower() for warning in result['warnings'])
    assert doc.metadata == metadata_before
    with Image.open(path) as im:
        assert im.n_frames == 2
        for index, expected in enumerate(frames):
            im.seek(index)
            im.load()
            assert im.size == expected.size
            assert np.array_equal(np.array(im.convert('RGB')), np.array(expected))
            e = im.getexif()
            assert e[274] == 1
            assert e[315] == 'Metadata regression'
            sub = e.get_ifd(34665)
            assert (sub[40962], sub[40963]) == expected.size
            assert sub[36867] == '2026:10:13 12:34:56'
            assert sub[37510] == b'ASCII\0\0\0Screenshot'
            assert e.get_ifd(40965)[1] == 'R98'
            assert tuple(e.get_ifd(34853)[2]) == (40, 30, 0)


def test_nested_exif_opt_out_and_misnamed_jpeg(tmp_path):
    exif = Image.Exif()
    exif[34665] = {40962: 28, 40963: 16, 37510: b'ASCII\0\0\0Screenshot'}
    source = tmp_path / 'jpeg-named-png.png'
    Image.new('RGB', (28, 16), 'white').save(source, format='JPEG', exif=exif)
    doc = load_document(source)
    path = tmp_path / 'output.tif'
    save_document(doc, path, 'TIFF', {'tiff_compression': 'group4', 'keep_exif': False})
    with Image.open(path) as im:
        im.load()
        assert im.size == (28, 16)
        assert 34665 not in im.getexif()
