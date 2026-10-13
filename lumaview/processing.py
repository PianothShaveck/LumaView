"""Deterministic, alpha-aware Advanced image operations.

Resize without resampling deliberately uses nearest-neighbour; with it enabled
Lanczos is the default. All operations return owned images and leave inputs intact.
"""
from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageColor, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from .model import ImageDocument, OPERATIONS, default_advanced

_FILTERS = {
    'nearest': Image.Resampling.NEAREST, 'bilinear': Image.Resampling.BILINEAR,
    'bicubic': Image.Resampling.BICUBIC, 'lanczos': Image.Resampling.LANCZOS,
    'box': Image.Resampling.BOX, 'hamming': Image.Resampling.HAMMING,
}


def _number(value, name, minimum=None, maximum=None):
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{name} must be a number.') from exc
    if not math.isfinite(result):
        raise ValueError(f'{name} must be finite.')
    if minimum is not None and result < minimum:
        raise ValueError(f'{name} must be at least {minimum}.')
    if maximum is not None and result > maximum:
        raise ValueError(f'{name} must be at most {maximum}.')
    return result


def _has_alpha(image):
    return 'A' in image.getbands() or 'transparency' in image.info


def _color(value, mode='RGBA'):
    try:
        return ImageColor.getcolor(str(value), mode)
    except ValueError as exc:
        raise ValueError(f'Invalid color: {value}') from exc


def _color_operation(image, function):
    """Transform color channels without changing coverage/alpha."""
    alpha = image.convert('RGBA').getchannel('A') if _has_alpha(image) else None
    rgb = image.convert('RGB')
    result = function(rgb)
    if alpha is not None:
        result = result.convert('RGBA')
        result.putalpha(alpha)
    return result


def _anchor_xy(anchor, width, height, item_width, item_height, x=0, y=0):
    anchor = str(anchor).lower().replace('_', '-')
    aliases = {'left':'center-left', 'right':'center-right', 'top':'top-center',
               'bottom':'bottom-center', 'centre':'center', 'middle':'center'}
    anchor = aliases.get(anchor, anchor)
    horizontal = 'left' if 'left' in anchor else 'right' if 'right' in anchor else 'center'
    vertical = 'top' if 'top' in anchor else 'bottom' if 'bottom' in anchor else 'center'
    px = x if horizontal == 'left' else width - item_width - x if horizontal == 'right' else (width - item_width) // 2 + x
    py = y if vertical == 'top' else height - item_height - y if vertical == 'bottom' else (height - item_height) // 2 + y
    return round(px), round(py)


def _geometry_mode(image):
    # Palette transparency must become coverage before interpolation.
    if _has_alpha(image):
        return image.convert('RGBA')
    if image.mode in ('1', 'P'):
        return image.convert('RGB')
    return image


def _crop(image, options):
    if not options['crop']:
        return image
    w = int(_number(options['crop_width'], 'Crop width', 0)) or image.width
    h = int(_number(options['crop_height'], 'Crop height', 0)) or image.height
    x, y = _anchor_xy(options['crop_anchor'], *image.size, w, h,
                      options['crop_x'], options['crop_y'])
    # Negative top/left coordinates count back from the opposite edge.
    anchor = options['crop_anchor']
    if 'left' in anchor and options['crop_x'] < 0:
        x = image.width + int(options['crop_x'])
    if 'top' in anchor and options['crop_y'] < 0:
        y = image.height + int(options['crop_y'])
    left, top = max(0, x), max(0, y)
    right, bottom = min(image.width, x + w), min(image.height, y + h)
    if right <= left or bottom <= top:
        raise ValueError('Crop rectangle does not intersect the image.')
    return image.crop((left, top, right, bottom))


def _resize(image, options, context):
    if not options['resize']:
        return image
    old_w, old_h = image.size
    mode = str(options['resize_mode']).replace('-', '_')
    aspect = bool(options['preserve_aspect'])
    if options['dpi_only']:
        new_dpi = _number(options['new_dpi'], 'New DPI', 1, 100000)
        old_dpi = context.get('dpi', (72, 72))
        old_x = _number(old_dpi[0], 'Original horizontal DPI', 0.001)
        old_y = _number(old_dpi[1], 'Original vertical DPI', 0.001)
        w, h = old_w * new_dpi / old_x, old_h * new_dpi / old_y
        aspect = old_x == old_y
    elif mode == 'percent':
        w = old_w * _number(options['percent_width'], 'Width percent', 0.001) / 100
        h = old_h * _number(options['percent_height'], 'Height percent', 0.001) / 100
        if aspect:
            h = old_h * w / old_w
    elif mode in ('long_side', 'long', 'longest'):
        scale = _number(options['long_side'], 'Long side', 1) / max(image.size)
        w, h = old_w * scale, old_h * scale
        aspect = True
    elif mode in ('short_side', 'short', 'shortest'):
        scale = _number(options['short_side'], 'Short side', 1) / min(image.size)
        w, h = old_w * scale, old_h * scale
        aspect = True
    elif mode in ('megapixels', 'mp'):
        scale = math.sqrt(_number(options['megapixels'], 'Megapixels', 0.000001) * 1_000_000 / (old_w * old_h))
        w, h = old_w * scale, old_h * scale
        aspect = True
    elif mode == 'dimensions':
        w = _number(options['width'], 'Width', 0)
        h = _number(options['height'], 'Height', 0)
        units = options['units']
        if units not in ('pixels', 'px'):
            dpi = float(options['new_dpi'] or context.get('dpi', (72, 72))[0] or 72)
            if units in ('cm', 'centimeters'):
                factor = dpi / 2.54
            elif units in ('inches', 'inch', 'in'):
                factor = dpi
            else:
                raise ValueError(f'Unknown dimension unit: {units}')
            w, h = w * factor, h * factor
        if not w and not h:
            return image
        if not w:
            w = old_w * h / old_h if aspect else old_w
        elif not h:
            h = old_h * w / old_w if aspect else old_h
        elif aspect:
            fill = options['size_bound'] in ('cover', 'fill')
            scale = (max if fill else min)(w / old_w, h / old_h)
            w, h = old_w * scale, old_h * scale
    else:
        raise ValueError(f'Unknown resize mode: {mode}')
    if options['no_enlarge'] and options['no_shrink']:
        return image
    if aspect:
        scale = w / old_w
        if options['no_enlarge']:
            scale = min(scale, 1)
        if options['no_shrink']:
            scale = max(scale, 1)
        w, h = old_w * scale, old_h * scale
    else:
        if options['no_enlarge']:
            w, h = min(w, old_w), min(h, old_h)
        if options['no_shrink']:
            w, h = max(w, old_w), max(h, old_h)
    size = (max(1, round(w)), max(1, round(h)))
    if size[0] * size[1] > 400_000_000:
        raise ValueError('Requested resize exceeds 400 megapixels.')
    name = str(options['resample_filter']).lower()
    if options['resample'] and name not in _FILTERS:
        raise ValueError(f'Unknown resampling filter: {options["resample_filter"]}')
    filter_ = _FILTERS[name] if options['resample'] else Image.Resampling.NEAREST
    if size != image.size:
        image = _geometry_mode(image).resize(size, filter_)
    return image


def _auto_crop(image, options):
    if not options['auto_crop']:
        return image
    rgba = image.convert('RGBA')
    background = Image.new('RGBA', image.size, rgba.getpixel((0, 0)))
    channels = ImageChops.difference(rgba, background).split()
    mask = channels[0]
    for channel in channels[1:]:
        mask = ImageChops.lighter(mask, channel)
    tolerance = int(_number(options['auto_crop_tolerance'], 'Auto-crop tolerance', 0, 255))
    mask = mask.point(lambda p: 255 if p > tolerance else 0)
    bounds = mask.getbbox()
    return image.crop(bounds) if bounds else image


def _rotate(image, options):
    if options['rotate_left']:
        image = image.transpose(Image.Transpose.ROTATE_90)
    if options['rotate_right']:
        image = image.transpose(Image.Transpose.ROTATE_270)
    return image


def _flip(image, options):
    if options['flip_horizontal']:
        image = ImageOps.mirror(image)
    if options['flip_vertical']:
        image = ImageOps.flip(image)
    return image


def _fine_rotation(image, options):
    if not options['fine_rotation']:
        return image
    image = _geometry_mode(image)
    if image.mode not in ('RGB', 'RGBA', 'L'):
        image = image.convert('RGBA' if _has_alpha(image) else 'RGB')
    fill = _color(options['rotation_color'], image.mode)
    # Positive angle is counterclockwise, matching Pillow and mathematical angles.
    return image.rotate(_number(options['rotation_angle'], 'Rotation angle'),
                        Image.Resampling.BICUBIC if options['resample'] else Image.Resampling.NEAREST,
                        expand=bool(options['rotation_expand']), fillcolor=fill)


def _canvas(image, options):
    if not options['canvas']:
        return image
    w, h = int(options['canvas_width']), int(options['canvas_height'])
    if options['canvas_relative']:
        w, h = image.width + w, image.height + h
    else:
        w, h = w or image.width, h or image.height
    if min(w, h) <= 0 or w * h > 400_000_000:
        raise ValueError('Canvas must be positive and no larger than 400 megapixels.')
    mode = 'RGBA' if _has_alpha(image) or len(_color(options['canvas_color'])) == 4 and _color(options['canvas_color'])[3] < 255 else 'RGB'
    result = Image.new(mode, (w, h), _color(options['canvas_color'], mode))
    x, y = _anchor_xy(options['canvas_anchor'], w, h, *image.size)
    # Paste without a mask preserves the original alpha rather than squaring it.
    result.paste(image.convert(mode), (x, y))
    return result


def _corrections(image, options):
    active = any(options[key] for key in ('brightness', 'contrast', 'gamma', 'saturation', 'balance_r', 'balance_g', 'balance_b'))
    if not active:
        return image
    def apply(rgb):
        if options['brightness']:
            amount = _number(options['brightness_value'], 'Brightness', -255, 255)
            rgb = rgb.point([round(max(0, min(255, p + amount))) for p in range(256)] * 3)
        if options['contrast']:
            value = _number(options['contrast_value'], 'Contrast', -127, 127)
            rgb = ImageEnhance.Contrast(rgb).enhance(1 + value / 127)
        if options['gamma']:
            gamma = _number(options['gamma_value'], 'Gamma', 0.001)
            rgb = rgb.point([round(255 * (p / 255) ** (1 / gamma)) for p in range(256)] * 3)
        if options['saturation']:
            value = _number(options['saturation_value'], 'Saturation', -255, 255)
            rgb = ImageEnhance.Color(rgb).enhance(1 + value / 255)
        channels = list(rgb.split())
        for i, name in enumerate(('r', 'g', 'b')):
            if options['balance_' + name]:
                value = _number(options['balance_' + name + '_value'], 'Color balance', -255, 255)
                channels[i] = channels[i].point([round(max(0, min(255, p + value))) for p in range(256)])
        return Image.merge('RGB', channels)
    return _color_operation(image, apply)


def _replace_color(image, options):
    if not options['replace_color']:
        return image
    source = _color(options['replace_from'], 'RGB')
    replacement = _color(options['replace_to'], 'RGB')
    tolerance = int(_number(options['replace_tolerance'], 'Color tolerance', 0, 255))
    rgb = image.convert('RGB')
    differences = ImageChops.difference(rgb, Image.new('RGB', image.size, source)).split()
    mask = ImageChops.lighter(ImageChops.lighter(differences[0], differences[1]), differences[2])
    mask = mask.point(lambda p: 255 if p <= tolerance else 0)
    output = Image.new('RGB', image.size, replacement)
    rgb.paste(output, (0, 0), mask)
    if _has_alpha(image):
        rgb.putalpha(image.convert('RGBA').getchannel('A'))
    return rgb


def _swap(image, options):
    order = str(options['swap_channels']).upper()
    if order == 'RGB':
        return image
    if sorted(order) != ['B', 'G', 'R']:
        raise ValueError('Channel order must be a permutation of RGB.')
    def swap(rgb):
        channels = dict(zip('RGB', rgb.split()))
        return Image.merge('RGB', [channels[name] for name in order])
    return _color_operation(image, swap)


def _effects(image, options):
    if not options['effects']:
        return image
    effect = str(options['effect']).lower()
    value = _number(options['effect_value'], 'Effect value', 0)
    filters = {'emboss': ImageFilter.EMBOSS, 'edge': ImageFilter.FIND_EDGES,
               'edges':ImageFilter.FIND_EDGES, 'contour':ImageFilter.CONTOUR,
               'smooth':ImageFilter.SMOOTH_MORE, 'detail':ImageFilter.DETAIL}
    def apply(rgb):
        if effect in filters:
            filtered = rgb.filter(filters[effect])
            return Image.blend(rgb, filtered, min(value, 1)) if value < 1 else filtered
        if effect == 'solarize':
            return ImageOps.solarize(rgb, threshold=max(0, min(255, round(value))))
        if effect == 'posterize':
            return ImageOps.posterize(rgb, bits=max(1, min(8, round(value))))
        if effect == 'sepia':
            sepia = ImageOps.colorize(ImageOps.grayscale(rgb), '#201008', '#ffe6b0')
            return Image.blend(rgb, sepia, min(value, 1))
        raise ValueError(f'Unknown effect: {effect}')
    return _color_operation(image, apply)


def _opaque_rgb(image):
    if not _has_alpha(image):
        return image.convert('RGB')
    rgba = image.convert('RGBA')
    white = Image.new('RGBA', image.size, (255, 255, 255, 255))
    return Image.alpha_composite(white, rgba).convert('RGB')


def _depth(image, options):
    if not options['depth']:
        return image
    mode = str(options['depth_mode']).lower().split()[0].replace('-bit', '')
    dither = Image.Dither.FLOYDSTEINBERG if options['dither'] else Image.Dither.NONE
    if mode in ('32', 'rgba'):
        return image.convert('RGBA')
    if mode in ('24', 'rgb'):
        # Explicit opaque depths composite alpha onto a white background.
        return _opaque_rgb(image)
    if mode in ('16', '565', 'rgb565'):
        # Pillow has no in-memory packed RGB565; quantize values to 5/6/5 bits.
        return _color_operation(image, lambda rgb: rgb.point(
            [round(round(p * 31 / 255) * 255 / 31) for p in range(256)] +
            [round(round(p * 63 / 255) * 255 / 63) for p in range(256)] +
            [round(round(p * 31 / 255) * 255 / 31) for p in range(256)]))
    if mode in ('1', 'monochrome', 'bilevel'):
        return _opaque_rgb(image).convert('L').convert('1', dither=dither)
    colors = int(options['custom_colors']) if mode == 'custom' else {'4':16, '8':256}.get(mode)
    if colors is None or not 2 <= colors <= 256:
        raise ValueError('Palette depth requires between 2 and 256 colors.')
    if options['best_palette']:
        # Adaptive palette generation followed by a separate mapping pass is
        # necessary for Pillow to actually apply Floyd–Steinberg dithering.
        rgb = _opaque_rgb(image)
        palette = rgb.quantize(colors=colors, method=Image.Quantize.MEDIANCUT)
        return rgb.quantize(palette=palette, dither=dither)
    rgb = _opaque_rgb(image)
    palette = rgb.quantize(colors=colors, method=Image.Quantize.FASTOCTREE)
    return rgb.quantize(palette=palette, dither=dither)


def _border(image, options):
    if not options['border']:
        return image
    widths = tuple(int(_number(options['border_' + edge], f'{edge.title()} border', 0)) for edge in ('left', 'top', 'right', 'bottom'))
    mode = 'RGBA' if _has_alpha(image) else 'RGB'
    return ImageOps.expand(image.convert(mode), widths, _color(options['border_color'], mode))


def expand_tokens(text, context=None):
    """Expand stable filename tokens shared by overlays and batch patterns."""
    context = context or {}
    source = Path(context['source']) if context.get('source') else None
    replacements = {'$F': source.name if source else '', '$N': source.stem if source else '',
                    '$E': source.suffix.lstrip('.') if source else '',
                    '$D': source.parent.name if source else '', '$I': str(context.get('index', 1)),
                    '$W': str(context.get('width', '')), '$H': str(context.get('height', ''))}
    return re.sub(r'\$[FNEDIWH]|#+',
                  lambda m: str(context.get('index', 1)).zfill(len(m[0])) if m[0].startswith('#') else replacements[m[0]],
                  str(text))


def _font(path, size):
    if path:
        try:
            return ImageFont.truetype(str(path), size)
        except OSError as exc:
            raise ValueError(f'Cannot open text font: {path}') from exc
    for candidate in ('/System/Library/Fonts/Supplemental/Arial.ttf',
                      '/System/Library/Fonts/Helvetica.ttc',
                      '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            pass
    return ImageFont.load_default(size=size)


def _text(image, options, context):
    if not options['text']:
        return image
    text = expand_tokens(options['text_value'], dict(context, width=image.width, height=image.height))
    if not text:
        return image
    size = int(_number(options['text_size'], 'Text size', 1, 4096))
    font = _font(options['text_font'], size)
    rgba = image.convert('RGBA')
    layer = Image.new('RGBA', image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    box = draw.multiline_textbbox((0, 0), text, font=font, spacing=4)
    w, h = box[2] - box[0], box[3] - box[1]
    x, y = _anchor_xy(options['text_anchor'], *image.size, w, h, options['text_x'], options['text_y'])
    opacity = _number(options['text_opacity'], 'Text opacity', 0, 100) / 100
    if options['text_background']:
        background = _color(options['text_background'])
        draw.rectangle((x-3, y-3, x+w+3, y+h+3), fill=background)
    color = _color(options['text_color'])
    draw.multiline_text((x-box[0], y-box[1]), text, font=font, fill=color, spacing=4)
    if opacity < 1:
        layer.putalpha(layer.getchannel('A').point(lambda p: round(p * opacity)))
    output = Image.alpha_composite(rgba, layer)
    return output if _has_alpha(image) else output.convert('RGB')


def _watermark(image, options):
    if not options['watermark']:
        return image
    path = Path(options['watermark_path'])
    if not path.is_file():
        raise ValueError('Choose an existing watermark image.')
    with Image.open(path) as handle:
        mark = ImageOps.exif_transpose(handle).convert('RGBA')
    scale = _number(options['watermark_scale'], 'Watermark scale', 0.01) / 100
    size = max(1, round(mark.width * scale)), max(1, round(mark.height * scale))
    if size[0] * size[1] > 400_000_000:
        raise ValueError('Watermark size exceeds 400 megapixels.')
    if size != mark.size:
        mark = mark.resize(size, Image.Resampling.LANCZOS if options['resample'] else Image.Resampling.NEAREST)
    opacity = _number(options['watermark_opacity'], 'Watermark opacity', 0, 100) / 100
    mark.putalpha(mark.getchannel('A').point(lambda p: round(p * opacity)))
    x, y = _anchor_xy(options['watermark_anchor'], *image.size, *mark.size,
                      options['watermark_x'], options['watermark_y'])
    output = image.convert('RGBA')
    output.alpha_composite(mark, (x, y))
    return output if _has_alpha(image) else output.convert('RGB')


def apply_operations(image: Image.Image, options: dict[str, Any] | None, context=None) -> Image.Image:
    options = {**default_advanced(), **(options or {})}
    context = dict(context or {})
    context.setdefault('dpi', image.info.get('dpi', (72, 72)))
    order = list(OPERATIONS)
    if options['custom_order']:
        requested = list(options['order'])
        invalid = set(requested) - set(OPERATIONS)
        if invalid or len(requested) != len(set(requested)):
            raise ValueError('Custom operation order contains invalid or duplicate operations.')
        order = requested + [item for item in OPERATIONS if item not in requested]
    handlers = {
        'crop': _crop, 'auto_crop': _auto_crop, 'rotate': _rotate, 'flip': _flip,
        'fine_rotation': _fine_rotation, 'canvas': _canvas,
        'corrections': _corrections, 'replace_color': _replace_color,
        'swap_channels': _swap, 'effects': _effects, 'depth': _depth,
        'border': _border, 'watermark': _watermark,
    }
    result = image.copy()
    for operation in order:
        if operation in handlers:
            result = handlers[operation](result, options)
        elif operation == 'resize':
            result = _resize(result, options, context)
        elif operation == 'text':
            result = _text(result, options, context)
        elif operation == 'grayscale' and options['grayscale']:
            result = _color_operation(result, ImageOps.grayscale)
        elif operation == 'negative' and options['negative']:
            result = _color_operation(result, ImageOps.invert)
        elif operation == 'auto_colors' and options['auto_colors']:
            result = _color_operation(result, lambda rgb: ImageOps.autocontrast(rgb, cutoff=0))
        elif operation == 'sharpen' and options['sharpen']:
            amount = _number(options['sharpen_value'], 'Sharpen amount', 0, 1000)
            result = _color_operation(result, lambda rgb: rgb.filter(ImageFilter.UnsharpMask(radius=2, percent=round(amount * 5), threshold=3)))
        elif operation == 'blur' and options['blur']:
            radius = _number(options['blur_value'], 'Blur radius', 0, 1000)
            result = _color_operation(result, lambda rgb: rgb.filter(ImageFilter.GaussianBlur(radius)))
    return result


def process_document(doc: ImageDocument, options=None, context=None) -> ImageDocument:
    options = {**default_advanced(), **(options or {})}
    result = doc.clone()
    frames = doc.frames if options['all_pages'] else doc.frames[:1]
    if not frames:
        raise ValueError('Image document contains no frames.')
    result.frames = [apply_operations(frame, options,
                       {'source': doc.source, 'index': i + 1, 'dpi': doc.dpi, **(context or {})}) for i, frame in enumerate(frames)]
    result.durations = list(doc.durations[:len(result.frames)])
    if options['resize'] and options['new_dpi']:
        dpi = _number(options['new_dpi'], 'DPI', 1, 100000)
        result.dpi = (dpi, dpi)
    for frame in result.frames:
        frame.info['dpi'] = result.dpi
    return result
