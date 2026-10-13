import os
import threading
from pathlib import Path

import pytest
from PIL import Image

from lumaview.batch import preview_batch, run_batch


def source(tmp_path, name='one.png', size=(16,12)):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGB',size,'red').save(path)
    return path


def test_conversion_has_real_resized_pixels_and_leaves_original(tmp_path):
    src = source(tmp_path)
    results = run_batch([src],tmp_path/'out','PNG',advanced={'resize':True,'width':8})
    assert results[0]['status'] == 'ok', results
    with Image.open(results[0]['output']) as handle:
        assert handle.size == (8,6)
    assert src.exists()


def test_rename_is_byte_exact_and_counter_tokens(tmp_path):
    src = source(tmp_path)
    planned = preview_batch([src],tmp_path/'out','PNG',mode='rename',pattern='$N_####',start=7,step=3)
    assert planned[0]['output'].name == 'one_0007.png'
    results = run_batch([src],tmp_path/'out','PNG',mode='rename',pattern='$N_####',start=7)
    assert results[0]['status'] == 'ok'
    assert Path(results[0]['output']).read_bytes() == src.read_bytes()


def test_preflight_rejects_input_overwrite_even_when_enabled(tmp_path):
    src = source(tmp_path)
    original = src.read_bytes()
    with pytest.raises(ValueError,match='input file'):
        run_batch([src],tmp_path,'PNG',advanced={'overwrite':True})
    assert src.read_bytes() == original


def test_collision_preflight_changes_nothing(tmp_path):
    a = source(tmp_path,'a/same.png')
    b = source(tmp_path,'b/same.png')
    output = tmp_path/'out'
    with pytest.raises(ValueError,match='Multiple'):
        run_batch([a,b],output,'PNG')
    assert not output.exists() and a.exists() and b.exists()


def test_existing_destination_never_silently_replaced(tmp_path):
    src = source(tmp_path)
    existing = source(tmp_path,'out/one.png',(4,4))
    old = existing.read_bytes()
    with pytest.raises(FileExistsError):
        run_batch([src],existing.parent,'PNG')
    assert existing.read_bytes() == old
    result = run_batch([src],existing.parent,'PNG',advanced={'overwrite':True})
    assert result[0]['status'] == 'ok'
    with Image.open(existing) as handle:
        assert handle.size == (16,12)


def test_case_alias_and_pattern_path_traversal_rejected(tmp_path):
    src = source(tmp_path)
    source(tmp_path,'out/ONE.png')
    with pytest.raises(FileExistsError):
        preview_batch([src],tmp_path/'out','PNG')
    with pytest.raises(ValueError,match='filename'):
        preview_batch([src],tmp_path/'out','PNG',mode='rename',pattern='../unsafe')


def test_subfolders_timestamps_and_original_delete(tmp_path):
    src = source(tmp_path,'inputs/nested/one.png')
    os.utime(src,ns=(1_600_000_000_000_000_000,1_600_000_000_000_000_000))
    result = run_batch([src],tmp_path/'out','PNG',advanced={'create_subfolders':True,'keep_datetime':True,'delete_originals':True},source_root=tmp_path/'inputs')
    assert result[0]['status'] == 'ok', result
    output = tmp_path/'out/nested/one.png'
    assert output.exists() and not src.exists()
    assert output.stat().st_mtime_ns == 1_600_000_000_000_000_000


def test_cancel_before_run_and_between_items(tmp_path):
    paths = [source(tmp_path,f'{i}.png') for i in range(3)]
    event = threading.Event()
    event.set()
    result = run_batch(paths,tmp_path/'out','PNG',cancel=event)
    assert all(item['status'] == 'cancelled' for item in result)
    assert not (tmp_path/'out').exists()
    event.clear()
    result = run_batch(paths,tmp_path/'out','PNG',cancel=event,callback=lambda result:event.set())
    assert [item['status'] for item in result] == ['ok','cancelled','cancelled']
    assert all(path.exists() for path in paths)


def test_encoder_failure_does_not_delete_original_or_leave_partial(tmp_path,monkeypatch):
    src = source(tmp_path)
    def broken(*args,**kwargs):
        Path(args[1]).write_bytes(b'broken')
        raise RuntimeError('simulated encoder failure')
    monkeypatch.setattr('lumaview.image_io.save_document',broken)
    result = run_batch([src],tmp_path/'out','PNG',advanced={'delete_originals':True})
    assert result[0]['status'] == 'error' and src.exists()
    assert list((tmp_path/'out').iterdir()) == []


def test_verify_failure_keeps_original(tmp_path,monkeypatch):
    src = source(tmp_path)
    def invalid(*args,**kwargs):
        Path(args[1]).write_bytes(b'not an image')
        return {'warnings':[]}
    monkeypatch.setattr('lumaview.image_io.save_document',invalid)
    result = run_batch([src],tmp_path/'out','PNG',advanced={'delete_originals':True})
    assert result[0]['status'] == 'error' and src.exists()
    assert not (tmp_path/'out/one.png').exists()


def test_multiframe_conversion_retains_original_when_frames_lost(tmp_path):
    src = tmp_path/'pages.tif'
    Image.new('RGB',(8,8),'red').save(src,save_all=True,append_images=[Image.new('RGB',(8,8),'blue')])
    result = run_batch([src],tmp_path/'out','JPEG',advanced={'delete_originals':True})
    assert result[0]['status'] == 'ok', result
    assert src.exists()
    assert any('fewer' in warning for warning in result[0]['warnings'])


def test_parallel_results_remain_in_input_order(tmp_path):
    paths = [source(tmp_path,f'{i}.png') for i in range(6)]
    calls = []
    result = run_batch(paths,tmp_path/'out','PNG',workers=3,callback=calls.append)
    assert all(item['status']=='ok' for item in result),result
    assert [item['source'] for item in result] == [str(path) for path in paths]
    assert len(calls) == 6


def test_filename_tokens_preserve_hashes_in_source_name(tmp_path):
    src = source(tmp_path,'photo##.png')
    plan = preview_batch([src],tmp_path/'out','PNG',mode='rename',pattern='$N_###',start=7)
    assert plan[0]['output'].name == 'photo##_007.png'


def test_cancellation_during_encode_does_not_commit_or_delete(tmp_path,monkeypatch):
    from lumaview import image_io
    src = source(tmp_path)
    event = threading.Event()
    original_save = image_io.save_document
    def save_and_cancel(*args,**kwargs):
        result = original_save(*args,**kwargs)
        event.set()
        return result
    monkeypatch.setattr(image_io,'save_document',save_and_cancel)
    result = run_batch([src],tmp_path/'out','PNG',advanced={'delete_originals':True},cancel=event)
    assert result[0]['status'] == 'cancelled' and src.exists()
    assert list((tmp_path/'out').iterdir()) == []


def test_batch_cannot_overwrite_its_watermark_resource(tmp_path):
    src = source(tmp_path,'one.png')
    watermark = source(tmp_path,'out/one.png')
    with pytest.raises(ValueError,match='watermark'):
        preview_batch([src],watermark.parent,'PNG',advanced={'watermark':True,'watermark_path':str(watermark),'overwrite':True})
