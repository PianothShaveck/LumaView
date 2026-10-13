from io import BytesIO

import numpy as np
import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication

from lumaview.batch import run_batch
from lumaview.image_io import load_document, save_document, supported_input_extensions
from lumaview.model import ImageDocument
from lumaview.processing import process_document


def test_compact_fax_reduces_flat_field_dots_without_resizing(tmp_path):
    pixels = np.full((256, 512), 190, dtype=np.uint8)
    pixels[:, :30] = 0
    pixels[:, -30:] = 255
    doc = ImageDocument([Image.fromarray(pixels)])
    sizes = {}
    for method in ('compact', 'floyd_steinberg', 'threshold'):
        path = tmp_path / f'{method}.tif'
        save_document(doc, path, 'TIFF', {'tiff_compression': 'group4', 'tiff_bilevel_method': method})
        with Image.open(path) as im:
            im.load()
            assert im.size == (512, 256)
            assert im.mode == '1'
            assert im.tag_v2[259] == 4
            assert im.getpixel((0, 0)) == 0
            assert im.getpixel((511, 0)) == 255
        sizes[method] = path.stat().st_size
    assert sizes['compact'] < sizes['floyd_steinberg'] / 4
    assert doc.frames[0].mode == 'L'
    assert np.array_equal(pixels, np.array(doc.frames[0]))


@pytest.mark.parametrize('method', ['adaptive', 'compact', 'threshold', 'floyd_steinberg'])
def test_already_bilevel_pixels_are_not_changed_by_tiff_conversion(tmp_path, method):
    original = Image.fromarray(np.random.default_rng(20).integers(0, 2, (77, 59), dtype='uint8').astype(bool))
    path = tmp_path / 'out.tif'
    save_document(ImageDocument([original]), path, 'TIFF',
                  {'tiff_compression': 'group4', 'tiff_bilevel_method': method, 'tiff_threshold': 230})
    with Image.open(path) as im:
        assert np.array_equal(np.array(original), np.array(im))


def test_resample_at_100_percent_does_not_change_pixels():
    original = Image.fromarray(np.random.default_rng(10).integers(0, 256, (41, 69, 3), dtype='uint8'))
    for enabled in (True, False):
        doc = process_document(ImageDocument([original]), {'resize': True, 'resize_mode': 'percent',
                               'percent_width': 100, 'percent_height': 100, 'resample': enabled})
        assert np.array_equal(np.array(doc.frames[0]), np.array(original))


@pytest.mark.parametrize('extension,header', [('.pld', b'Photo Line Document\0'),
                                            ('.plb', b'Photo Line Browsefile\0')])
def test_photoline_preview_is_labelled_and_original_is_retained(tmp_path, extension, header):
    preview = BytesIO()
    Image.new('RGB', (52, 37), 'orange').save(preview, format='JPEG')
    source = tmp_path / ('layered' + extension)
    original = header + b'\0' * 52 + preview.getvalue() + b'Layer data retained in original'
    source.write_bytes(original)
    assert extension in supported_input_extensions()
    doc = load_document(source)
    assert doc.frames[0].size == (52, 37)
    assert doc.metadata['preview_only'] is True
    assert doc.metadata['warnings']
    results = run_batch([source], tmp_path / 'output', 'TIFF', {'tiff_compression': 'group4'},
                        {'delete_originals': True})
    assert results[0]['status'] == 'ok'
    assert source.read_bytes() == original
    assert any('Original retained' in warning for warning in results[0]['warnings'])


def test_no_photoline_signature_is_not_treated_as_a_preview(tmp_path):
    path = tmp_path / 'not-an-image.pld'
    path.write_bytes(b'Other file format')
    with pytest.raises(ValueError, match='Not a recognized PhotoLine'):
        load_document(path)


def test_tiff_controls_store_the_selected_method():
    from lumaview.ui.format_options import FormatOptionsPanel
    app = QApplication.instance() or QApplication([])
    panel = FormatOptionsPanel('TIFF', {'tiff_compression': 'group4'})
    assert panel.values()['tiff_bilevel_method'] == 'adaptive'
    assert panel.widgets['tiff_adaptive_alpha_background'].isEnabled()
    assert not panel.widgets['tiff_adaptive_alpha_background'].isChecked()
    assert not panel.widgets['tiff_bilevel_contrast'].isEnabled()
    assert not panel.widgets['tiff_threshold'].isEnabled()
    method = panel.widgets['tiff_bilevel_method']
    method.setCurrentIndex(method.findData('compact'))
    assert panel.widgets['tiff_bilevel_contrast'].isEnabled()
    assert not panel.widgets['tiff_adaptive_alpha_background'].isEnabled()
    method.setCurrentIndex(method.findData('threshold'))
    panel.widgets['tiff_threshold'].setValue(160)
    assert panel.values()['tiff_threshold'] == 160
    assert panel.widgets['tiff_threshold'].isEnabled()
    assert not panel.widgets['tiff_bilevel_contrast'].isEnabled()
    compression = panel.widgets['tiff_compression']
    compression.setCurrentIndex(compression.findData('tiff_lzw'))
    assert not method.isEnabled()
    panel.deleteLater()
    app.processEvents()
