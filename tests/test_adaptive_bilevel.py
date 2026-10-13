import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest
from PIL import Image
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from lumaview.bilevel import adaptive_bilevel
from lumaview.batch import run_batch
from lumaview.image_io import load_document, save_document
from lumaview.model import ImageDocument
from lumaview.ui.settings import upgrade_settings


def test_adaptive_dot_pattern_is_stable_across_threads():
    # Synthetic regression fixture, cross-checked with an independent integer
    # implementation. No private images or external application are required.
    rgb = np.random.default_rng(192).integers(0, 256, (91, 107, 3), dtype=np.uint8)
    original = Image.fromarray(rgb)
    def convert(_):
        return hashlib.sha256(adaptive_bilevel(original).tobytes()).hexdigest()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(convert, range(4)))
    assert results == ['a98f8e5eb568371c70c29f6974aa81ed78fd9a2a6c76d3774e4bcf95bc4379af'] * 4
    assert np.array_equal(np.asarray(original), rgb)


@pytest.mark.parametrize('mode', ['1', 'L', 'RGB', 'RGBA', 'P'])
@pytest.mark.parametrize('value', [0, 255])
def test_uniform_black_and_white_remain_uniform(mode, value):
    source = Image.new('L', (19, 11), value).convert(mode)
    result = adaptive_bilevel(source)
    assert result.size == source.size
    assert result.mode == '1'
    assert result.getextrema() == (value, value)


def test_alpha_choice_changes_only_the_encoded_image(tmp_path):
    rgba = Image.new('RGBA', (60, 40), (0, 0, 0, 0))
    rgba.paste((255, 255, 255, 255), (30, 0, 60, 40))
    original = rgba.tobytes()
    for blend in [False, True]:
        path = tmp_path / f'{blend}.tif'
        save_document(ImageDocument([rgba]), path, 'TIFF', {
            'tiff_compression': 'group4', 'tiff_adaptive_alpha_background': blend})
        with Image.open(path) as result:
            assert result.getpixel((10, 10)) == (255 if blend else 0)
            assert result.getpixel((40, 10)) == 255
    assert rgba.tobytes() == original


def test_input_resolution_is_independent_of_pdf_export_options(tmp_path):
    source = tmp_path / 'page.pdf'
    save_document(ImageDocument([Image.new('RGB', (144, 72), 'white')]), source, 'PDF',
                  {'pdf_dpi': 144, 'pdf_compression': 'lossless'})
    assert load_document(source).frames[0].size == (96, 48)
    results = run_batch([source], tmp_path / 'out', 'TIFF',
                        {'tiff_compression': 'group4', 'pdf_dpi': 300},
                        input_options={'pdf_dpi': 192})
    assert results[0]['status'] == 'ok'
    with Image.open(results[0]['output']) as image:
        assert image.size == (192, 96)


def test_svg_transparent_canvas_remains_legible_in_monochrome(tmp_path):
    app = QApplication.instance() or QApplication([])
    source = tmp_path / 'shape.svg'
    source.write_text('<svg viewBox="0 0 40 30" xmlns="http://www.w3.org/2000/svg">'
                      '<rect x="10" y="10" width="20" height="10"/></svg>')
    doc = load_document(source, svg_size=(80, 60))
    assert doc.frames[0].getpixel((0, 0))[3] == 0
    path = tmp_path / 'shape.tif'
    save_document(doc, path, 'TIFF', {'tiff_compression': 'group4'})
    with Image.open(path) as result:
        assert result.getpixel((0, 0)) == 255
        assert result.getpixel((40, 30)) == 0


def test_fax_pages_keep_pixels_with_one_miniswhite_strip(tmp_path):
    rng = np.random.default_rng(9)
    frames = [Image.fromarray(rng.integers(0, 2, shape, dtype=np.uint8).astype(bool))
              for shape in ((801, 709), (73, 1143))]
    path = tmp_path / 'pages.tif'
    save_document(ImageDocument(frames), path, 'TIFF', {'tiff_compression': 'group4'})
    with Image.open(path) as result:
        for index, original in enumerate(frames):
            result.seek(index)
            assert result.tag_v2[262] == 0
            assert len(result.tag_v2[273]) == 1
            assert result.tag_v2[278] == original.height
            assert np.array_equal(np.asarray(result), np.asarray(original))


def test_upgrade_changes_old_defaults_but_keeps_presets_and_custom_contrast(tmp_path):
    settings = QSettings(str(tmp_path / 'settings.ini'), QSettings.Format.IniFormat)
    legacy = {'tiff_bilevel_method': 'compact', 'tiff_bilevel_contrast': 3.0}
    custom = {**legacy, 'tiff_bilevel_contrast': 4.0}
    settings.setValue('format/TIFF', json.dumps(legacy))
    settings.setValue('batch/options', json.dumps({'format_options': {'TIFF': custom}}))
    settings.setValue('presets/TIFF', json.dumps({'My preset': legacy}))
    upgrade_settings(settings)
    assert json.loads(settings.value('format/TIFF'))['tiff_bilevel_method'] == 'adaptive'
    assert json.loads(settings.value('batch/options'))['format_options']['TIFF'] == custom
    assert json.loads(settings.value('presets/TIFF'))['My preset'] == legacy
    # A later explicit choice of the legacy method must not be migrated again.
    settings.setValue('format/TIFF', json.dumps(legacy))
    upgrade_settings(settings)
    assert json.loads(settings.value('format/TIFF')) == legacy
