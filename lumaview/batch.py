"""Collision-safe conversion and copying with predictable batch names.

Rename mode copies file bytes under a new name. Original deletion is opt-in and
occurs only after the committed output has passed verification. Conversion never
writes over any member of its input set, even when overwrite is selected.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .model import EXTENSIONS, default_advanced, default_input
from .processing import expand_tokens, process_document

_MODES = {'convert':'convert', 'rename':'rename', 'convert_rename':'convert_rename',
          'both':'convert_rename', 'convert+rename':'convert_rename'}


def _path_key(path):
    # macOS normally ignores case and canonical Unicode composition.
    return unicodedata.normalize('NFD', str(Path(path).resolve())).casefold()


def _cancelled(cancel):
    if cancel is None:
        return False
    if hasattr(cancel, 'is_set'):
        return bool(cancel.is_set())
    if callable(cancel):
        return bool(cancel())
    return bool(cancel)


def _sequence(pattern, source, index):
    name = expand_tokens(pattern, {'source': source, 'index': index})
    if not name or name in ('.', '..') or any(char in name for char in ('/', '\\', '\0', ':')):
        raise ValueError('The rename pattern must produce a filename without folders or colons.')
    return name


def preview_batch(paths, output_dir, fmt, mode='convert', pattern='$N_###',
                  start=1, step=1, source_root=None, advanced=None):
    """Resolve and preflight every target before creating or modifying any file."""
    advanced = {**default_advanced(), **(advanced or {})}
    try:
        mode = _MODES[mode]
    except KeyError as exc:
        raise ValueError(f'Unknown batch mode: {mode}') from exc
    fmt = str(fmt).upper()
    if mode != 'rename' and fmt not in EXTENSIONS:
        raise ValueError(f'Unknown output format: {fmt}')
    sources = [Path(path).expanduser().absolute() for path in paths]
    if not sources:
        return []
    start, step = int(start), int(step)
    if start < 0 or step < 1:
        raise ValueError('Start must be non-negative and increment must be positive.')
    root = Path(output_dir).expanduser().absolute()
    if root.exists() and not root.is_dir():
        raise ValueError('Output location is not a folder.')
    source_keys = {_path_key(path) for path in sources}
    resource_keys = {_path_key(advanced[key]) for enabled, key in
                     (('watermark', 'watermark_path'), ('text', 'text_font'))
                     if advanced[enabled] and advanced[key]}
    if len(source_keys) != len(sources):
        raise ValueError('The input list contains a duplicate file or an alias of the same file.')
    for path in sources:
        if not path.is_file():
            raise FileNotFoundError(f'Input file does not exist: {path}')
    tree_root = Path(source_root).expanduser().absolute() if source_root else Path(os.path.commonpath([str(path.parent) for path in sources]))
    targets = set()
    directory_entries = {}
    plan = []
    for position, source in enumerate(sources):
        index = start + position * step
        name = source.stem if mode == 'convert' else _sequence(pattern, source, index)
        extension = source.suffix if mode == 'rename' else EXTENSIONS[fmt]
        # $F is a complete filename; avoid adding its existing extension twice.
        if not name.lower().endswith(extension.lower()) or not extension:
            name += extension
        parent = root
        if advanced['create_subfolders']:
            try:
                relative = source.parent.relative_to(tree_root)
            except ValueError as exc:
                raise ValueError(f'Input is outside the source root: {source}') from exc
            parent = root / relative
        output = parent / name
        key = _path_key(output)
        if key in source_keys:
            raise ValueError(f'Output would overwrite an input file: {output.name}. Choose another folder or name.')
        if key in resource_keys:
            raise ValueError(f'Output would overwrite a watermark or font used by this batch: {output.name}.')
        if key in targets:
            raise ValueError(f'Multiple input files would produce {output.name}. Use a numbered rename pattern.')
        if output.exists():
            if not output.is_file():
                raise FileExistsError(f'Output is not a regular file: {output}')
            if not advanced['overwrite']:
                raise FileExistsError(f'Output already exists: {output}. Enable overwrite or choose another name.')
        # Even on a case-sensitive development machine, catch collisions that a
        # default macOS filesystem would treat as the same filename.
        if parent.is_dir():
            if parent not in directory_entries:
                cached = {}
                for entry in parent.iterdir():
                    cached.setdefault(_path_key(entry), []).append(entry)
                directory_entries[parent] = cached
            aliases = [entry for entry in directory_entries[parent].get(key, ()) if entry != output]
            if aliases:
                raise FileExistsError(f'Output conflicts with an existing filename: {aliases[0].name}')
        targets.add(key)
        plan.append({'source':source, 'output':output, 'index':index})
    return plan


def _digest(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.digest()


def _signature(path):
    stat = Path(path).stat()
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)


def _verify_conversion(path, fmt, processed, format_options, all_pages=False):
    from .image_io import load_document
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError('Encoder produced an empty output.')
    if fmt == 'RAW':
        mode = format_options.get('raw_mode', 'RGB')
        bands = {'1':None, 'L':1, 'RGB':3, 'RGBA':4, 'CMYK':4}.get(mode)
        w, h = processed.frames[0].size
        if bands is None:
            expected = ((w + 7) // 8) * h
        else:
            expected = w * h * bands
        if path.stat().st_size != expected:
            raise ValueError('RAW output has an unexpected byte count.')
        return 1
    if fmt == 'EPS':
        # Pillow can encode EPS without Ghostscript; decoding requires it.
        content = path.read_bytes()
        if not content.startswith(b'%!PS-Adobe') or b'%%BoundingBox:' not in content or b'%%EOF' not in content[-256:]:
            raise ValueError('EPS output is incomplete.')
        return 0  # Structural verification cannot authorize original deletion.
    # PDF page dimensions depend on export DPI and paper size; a successful
    # rasterization verifies that the output is readable without assuming pixels.
    try:
        reopened = load_document(path, all_pages=all_pages, password=format_options.get('pdf_password') or None)
    except Exception as exc:
        raise ValueError(f'Cannot verify the encoded output: {exc}') from exc
    if not reopened.frames or min(reopened.frames[0].size) < 1:
        raise ValueError('Encoded output contains no valid image.')
    return len(reopened.frames)


def _commit(temp, output, overwrite):
    if overwrite:
        os.replace(temp, output)
    else:
        # Unlike os.replace, link fails if another process created the target
        # after preflight. Both paths live in the same directory/filesystem.
        os.link(temp, output)
        temp.unlink()


def run_batch(paths, output_dir, fmt, format_options=None, advanced=None,
              mode='convert', pattern='$N_###', start=1, step=1,
              callback=None, cancel=None, workers=1, source_root=None, input_options=None):
    """Run a fully preflighted batch and return results in input order.

    ``callback(result)`` runs on the calling thread. ``cancel`` may be a callable
    or threading.Event. Completed items are kept; in-flight uncommitted items
    discard temporary output. Rename mode is a verified copy, never a blind move.
    """
    advanced = {**default_advanced(), **(advanced or {})}
    format_options = dict(format_options or {})
    input_options = {**default_input(), **(input_options or {})}
    fmt = str(fmt).upper()
    mode = _MODES.get(mode, mode)
    plan = preview_batch(paths, output_dir, fmt, mode, pattern, start, step, source_root, advanced)
    if not plan:
        return []
    from .image_io import load_document, save_document
    workers = max(1, min(16, int(workers)))

    def process(item):
        source, output, index = item['source'], item['output'], item['index']
        result = {'source':str(source), 'output':str(output), 'index':index, 'status':'cancelled', 'warnings':[]}
        temp = None
        if _cancelled(cancel):
            return result
        try:
            before = _signature(source)
            source_stat = source.stat()
            output.parent.mkdir(parents=True, exist_ok=True)
            descriptor, name = tempfile.mkstemp(prefix='.lumaview-', suffix=output.suffix, dir=output.parent)
            os.close(descriptor)
            temp = Path(name)
            if mode == 'rename':
                shutil.copyfile(source, temp)
                if _digest(source) != _digest(temp):
                    raise ValueError('Output verification failed: copied bytes differ from input.')
                source_pages = output_pages = 1
            else:
                # Load all pages even when processing only the first, so deletion
                # can never discard unnoticed pages from the source document.
                doc = load_document(source, pdf_dpi=input_options['pdf_dpi'], all_pages=True,
                                    svg_size=(input_options['svg_width'], input_options['svg_height']),
                                    svg_keep_aspect=input_options['svg_keep_aspect'])
                source_pages = len(doc.frames)
                processed = process_document(doc, advanced, context={'index': index})
                saved = save_document(processed, temp, fmt, format_options) or {}
                result['warnings'].extend(saved.get('warnings', []))
                for key in ('bytes', 'pixel_size', 'color_mode', 'compression'):
                    if key in saved:
                        result[key] = saved[key]
                output_pages = _verify_conversion(temp, fmt, processed, format_options,
                                                  all_pages=bool(advanced['delete_originals']))
            if _signature(source) != before:
                raise ValueError('Input changed during processing. Output was not committed.')
            if _cancelled(cancel):
                return result
            if advanced['keep_datetime']:
                os.utime(temp, ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns))
            _commit(temp, output, bool(advanced['overwrite']))
            temp = None
            result['status'] = 'ok'
            if advanced['delete_originals']:
                if _cancelled(cancel):
                    result['warnings'].append('Original retained because the batch was cancelled.')
                elif fmt == 'EPS' and mode != 'rename':
                    result['warnings'].append('Original retained because EPS was verified structurally without a PostScript renderer.')
                elif mode != 'rename' and doc.metadata.get('preview_only'):
                    result['warnings'].append('Original retained because only an embedded preview was converted.')
                elif source.suffix.lower() == '.pdf' and mode != 'rename':
                    result['warnings'].append('Original PDF retained because raster conversion cannot preserve searchable text, links or vectors.')
                elif output_pages < source_pages:
                    result['warnings'].append('Original retained because the output contains fewer pages or frames.')
                elif _signature(source) != before:
                    result['warnings'].append('Original retained because it changed during processing.')
                else:
                    try:
                        source.unlink()
                        result['original_deleted'] = True
                    except OSError as exc:
                        result['warnings'].append(f'Output saved, but original could not be deleted: {exc}')
            return result
        except Exception as exc:
            result['status'] = 'error'
            result['error'] = str(exc)
            return result
        finally:
            if temp is not None:
                temp.unlink(missing_ok=True)

    results = [None] * len(plan)
    if workers == 1:
        for position, item in enumerate(plan):
            result = process(item)
            results[position] = result
            if callback:
                callback(dict(result))
    else:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix='LumaView') as pool:
            futures = {pool.submit(process, item):position for position,item in enumerate(plan)}
            for future in as_completed(futures):
                result = future.result()
                results[futures[future]] = result
                if callback:
                    callback(dict(result))
    return results
