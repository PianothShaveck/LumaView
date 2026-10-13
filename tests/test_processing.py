import math
from pathlib import Path

import pytest
from PIL import Image

from lumaview.model import ImageDocument
from lumaview.processing import apply_operations, process_document


def image(w=100, h=50, mode='RGB', color='white'):
    return Image.new(mode, (w, h), color)


@pytest.mark.parametrize('options,expected', [
    ({'width':40,'height':40}, (40,20)),
    ({'width':40,'height':40,'preserve_aspect':False}, (40,40)),
    ({'width':40,'height':40,'size_bound':'fill'}, (80,40)),
    ({'width':0,'height':20}, (40,20)),
    ({'resize_mode':'long_side','long_side':80}, (80,40)),
    ({'resize_mode':'short_side','short_side':80}, (160,80)),
    ({'resize_mode':'megapixels','megapixels':0.02}, (200,100)),
    ({'resize_mode':'percent','percent_width':50,'percent_height':30}, (50,25)),
    ({'resize_mode':'percent','percent_width':50,'percent_height':30,'preserve_aspect':False}, (50,15)),
    ({'width':300,'height':300,'no_enlarge':True}, (100,50)),
    ({'width':20,'height':20,'no_shrink':True}, (100,50)),
    ({'width':300,'no_shrink':True,'no_enlarge':True}, (100,50)),
    ({'width':1,'height':1,'units':'inches','new_dpi':100,'preserve_aspect':False}, (100,100)),
    ({'width':2.54,'height':2.54,'units':'cm','new_dpi':100,'preserve_aspect':False}, (100,100)),
])
def test_resize_dimensions(options, expected):
    assert apply_operations(image(), {'resize':True, **options}).size == expected


def test_resample_is_meaningfully_different_and_does_not_mutate_source():
    original = Image.new('RGB',(8,1))
    original.putdata([(0,0,0)] * 4 + [(255,255,255)] * 4)
    common = {'resize':True,'width':3,'height':1,'preserve_aspect':False}
    nearest = apply_operations(original, {**common,'resample':False})
    lanczos = apply_operations(original, {**common,'resample':True})
    assert set(nearest.getdata()) <= {(0,0,0),(255,255,255)}
    assert any(0 < value[0] < 255 for value in lanczos.getdata())
    assert original.size == (8,1)


def test_resize_alpha_has_no_black_fringes():
    original = Image.new('RGBA',(8,2),(0,0,0,0))
    for y in range(2):
        for x in range(4):
            original.putpixel((x,y),(255,0,0,255))
    output = apply_operations(original, {'resize':True,'width':4,'height':1,'preserve_aspect':False})
    for r,g,b,a in output.getdata():
        if a:
            assert r == 255 and g == b == 0


def test_physical_size_uses_document_dpi_and_dpi_ratio_resizes():
    doc = ImageDocument([image()], dpi=(100,100))
    physical = process_document(doc, {'resize':True,'width':2,'units':'inches'})
    assert physical.frames[0].size == (200,100)
    dpi_only = process_document(doc, {'resize':True,'width':5,'dpi_only':True,'new_dpi':300})
    assert dpi_only.frames[0].size == (300,150)
    metadata_only = process_document(doc, {'resize':True,'new_dpi':300})
    assert metadata_only.frames[0].size == doc.frames[0].size
    assert metadata_only.dpi == (300,300)
    assert dpi_only.dpi == (300,300) and doc.dpi == (100,100)


def test_crop_anchor_and_negative_coordinates():
    src = image()
    result = apply_operations(src, {'crop':True,'crop_width':20,'crop_height':10,'crop_anchor':'bottom-right'})
    assert result.size == (20,10)
    result = apply_operations(src, {'crop':True,'crop_x':-10,'crop_y':-5,'crop_width':10,'crop_height':5})
    assert result.size == (10,5)
    with pytest.raises(ValueError, match='intersect'):
        apply_operations(src, {'crop':True,'crop_x':200})


def test_alpha_survives_all_color_operations():
    src = image(8,8,'RGBA',(40,100,200,77))
    output = apply_operations(src, {'negative':True,'grayscale':True,'auto_colors':True,
       'brightness':True,'brightness_value':12,'contrast':True,'contrast_value':5,
       'gamma':True,'gamma_value':1.4,'saturation':True,'saturation_value':50,
       'balance_r':True,'balance_r_value':10,'swap_channels':'BGR','effects':True,
       'sharpen':True,'blur':True})
    assert output.getchannel('A').getextrema() == (77,77)
    assert src.getpixel((0,0)) == (40,100,200,77)


def test_gamma_brightness_replace_and_channel_values():
    source = image(1,1,color=(64,128,200))
    gamma = apply_operations(source, {'gamma':True,'gamma_value':2})
    assert gamma.getpixel((0,0)) == tuple(round(255 * math.sqrt(v/255)) for v in (64,128,200))
    changed = apply_operations(source, {'replace_color':True,'replace_from':'#4080c8','replace_to':'#ff0000','swap_channels':'BGR'})
    assert changed.getpixel((0,0)) == (0,0,255)


def test_palette_dither_and_565():
    source = Image.linear_gradient('L').resize((256,64)).convert('RGB')
    opts = {'depth':True,'depth_mode':'custom','custom_colors':4}
    plain = apply_operations(source,{**opts,'dither':False})
    dithered = apply_operations(source,{**opts,'dither':True})
    assert len(plain.getcolors()) <= 4
    assert plain.tobytes() != dithered.tobytes()
    quantized = apply_operations(image(1,1,color=(123,45,67)), {'depth':True,'depth_mode':'16'})
    assert quantized.getpixel((0,0)) == (123,45,66)


def test_canvas_preserves_alpha_without_double_masking_and_border():
    source = image(2,2,'RGBA',(255,0,0,128))
    result = apply_operations(source, {'canvas':True,'canvas_width':4,'canvas_height':4,'canvas_color':'#00000000'})
    assert result.getpixel((1,1)) == (255,0,0,128)
    assert result.getpixel((0,0)) == (0,0,0,0)
    result = apply_operations(source, {'border':True,'border_left':1,'border_top':2,'border_right':3,'border_bottom':4})
    assert result.size == (6,8)


def test_auto_crop_tolerance_and_operation_order():
    source = image(20,20)
    source.paste((0,0,0),(5,5,15,15))
    assert apply_operations(source, {'auto_crop':True}).size == (10,10)
    opts = {'crop':True,'crop_width':10,'crop_height':10,'resize':True,'width':5,'height':5}
    normal = apply_operations(source, opts)
    custom = apply_operations(source, {**opts,'custom_order':True,'order':['resize','crop']})
    assert normal.size == (5,5)
    assert custom.size == (5,5)
    assert normal.tobytes() != custom.tobytes()
    with pytest.raises(ValueError):
        apply_operations(source, {'custom_order':True,'order':['resize','resize']})


def test_text_watermark_rotation_and_pages(tmp_path):
    mark = tmp_path / 'watermark.png'
    image(2,2,'RGBA',(255,0,0,255)).save(mark)
    output = apply_operations(image(10,10,color='black'), {'watermark':True,'watermark_path':str(mark),
             'watermark_anchor':'top-left','watermark_x':0,'watermark_y':0,'watermark_opacity':50})
    assert output.getpixel((0,0)) == (128,0,0)
    assert output.getpixel((3,3)) == (0,0,0)
    text = apply_operations(image(150,80,color='black'), {'text':True,'text_value':'$N','text_anchor':'top-left','text_x':0,'text_y':0}, {'source':Path('test.png')})
    assert text.getbbox()
    doc = ImageDocument([image(),image(20,10)],durations=[10,20], loop=2)
    processed = process_document(doc, {'rotate_left':True,'all_pages':False})
    assert len(processed.frames) == 1 and processed.frames[0].size == (50,100)
    assert processed.durations == [10] and processed.loop == 2


def test_native_ocr_fails_clearly_off_macos(monkeypatch):
    from lumaview.native_macos import recognize_text
    monkeypatch.setattr('lumaview.native_macos.sys.platform','linux')
    with pytest.raises(RuntimeError,match='OCR requires macOS'):
        recognize_text(image())


def test_minimum_size_keeps_entire_image_without_cropping():
    source = Image.new('RGB',(100,50),'white')
    source.paste('red',(0,0,10,50))
    source.paste('blue',(90,0,100,50))
    result = apply_operations(source, {'resize':True,'width':40,'height':40,'size_bound':'fill','resample':False})
    assert result.size == (80,40)
    assert result.getpixel((0,20)) == (255,0,0)
    assert result.getpixel((79,20)) == (0,0,255)


def test_high_bit_depth_identity_and_resize_preserve_16bit_grayscale():
    source = Image.new('I;16',(8,8),32000)
    source.putpixel((3,3),45000)
    copied = apply_operations(source,{})
    assert copied.mode == 'I;16' and copied.tobytes() == source.tobytes()
    assert copied is not source
    doc = ImageDocument([source],dpi=(300,300))
    processed = process_document(doc,{})
    assert processed.frames[0].mode == 'I;16'
    assert processed.frames[0].getpixel((3,3)) == 45000
    resized = apply_operations(Image.new('I;16',(8,8),32000), {'resize':True,'width':4})
    assert resized.mode == 'I;16' and resized.getpixel((1,1)) == 32000


def test_ui_adjustment_extremes_are_supported():
    source = image(2,2,color=(200,50,100))
    neutral = apply_operations(source, {'saturation':True,'saturation_value':-255})
    r,g,b = neutral.getpixel((0,0))
    assert r == g == b
    for contrast in (-127,127):
        for saturation in (-255,255):
            result = apply_operations(source, {'contrast':True,'contrast_value':contrast,'saturation':True,'saturation_value':saturation})
            assert result.size == source.size


def test_token_expansion_is_single_pass_and_includes_numbered_overlays():
    from lumaview.processing import expand_tokens
    assert expand_tokens('$N_###_$I', {'source':Path('photo##.png'),'index':7}) == 'photo##_007_7'


def test_disabled_options_do_not_apply_stale_values():
    source = image(3,2,'RGBA',(12,34,56,78))
    result = apply_operations(source, {'resize':False,'width':100,'resample_filter':'bad',
       'crop':False,'crop_x':9999,'gamma':False,'gamma_value':0,
       'custom_order':False,'order':['invalid'],'text':False,'text_font':'missing.ttf',
       'watermark':False,'watermark_path':'missing.png'})
    assert result.mode == source.mode and result.tobytes() == source.tobytes()


def test_best_palette_selects_real_quantization_algorithm():
    source = Image.new('RGB',(40,40))
    source.putdata([((x*17+y*3)%256,(x*4+y*23)%256,(x*31+y*5)%256) for y in range(40) for x in range(40)])
    options = {'depth':True,'depth_mode':'custom','custom_colors':16,'dither':False}
    best = apply_operations(source,{**options,'best_palette':True})
    fast = apply_operations(source,{**options,'best_palette':False})
    assert best.convert('RGB').tobytes() != fast.convert('RGB').tobytes()
    assert len(best.getcolors()) <=16 and len(fast.getcolors()) <=16


def test_crop_signed_offsets_and_anchors_choose_expected_pixels():
    source = Image.new('RGB',(10,10))
    source.putdata([(x,y,0) for y in range(10) for x in range(10)])
    last = apply_operations(source, {'crop':True,'crop_x':-2,'crop_y':-3,'crop_width':2,'crop_height':3})
    assert last.getpixel((0,0)) == (8,7,0)
    opposite = apply_operations(source, {'crop':True,'crop_anchor':'bottom-right','crop_x':1,'crop_y':2,'crop_width':3,'crop_height':4})
    assert opposite.size == (3,4) and opposite.getpixel((0,0)) == (6,4,0)
    center = apply_operations(source, {'crop':True,'crop_anchor':'center','crop_x':-1,'crop_y':1,'crop_width':4,'crop_height':4})
    assert center.getpixel((0,0)) == (2,4,0)


@pytest.mark.parametrize('depth,expected_mode', [('1','1'),('4','P'),('8','P'),('custom','P'),('24','RGB'),('32','RGBA')])
def test_explicit_bit_depth_has_correct_mode_and_alpha_policy(depth,expected_mode):
    source = image(4,4,'RGBA',(255,0,0,128))
    output = apply_operations(source, {'depth':True,'depth_mode':depth,'dither':False})
    assert output.mode == expected_mode
    if depth == '32':
        assert output.getpixel((0,0)) == (255,0,0,128)
    elif depth == '1':
        assert output.getpixel((0,0)) == 255
    else:
        assert output.convert('RGB').getpixel((0,0)) == (255,127,127)
    assert source.getpixel((0,0)) == (255,0,0,128)


def test_default_final_depth_applies_after_border_and_overlays():
    source = Image.new('RGB',(40,40))
    source.putdata([((x*17+y*3)%256,(x*4+y*23)%256,(x*31+y*5)%256) for y in range(40) for x in range(40)])
    options = {'depth':True,'depth_mode':'4','border':True,'border_color':'#123456',
               'border_left':1,'border_right':1,'border_top':1,'border_bottom':1,
               'text':True,'text_value':'Test','text_size':12,'text_x':0,'text_y':0}
    result = apply_operations(source,options)
    assert result.mode == 'P'
    assert result.size == (42,42)
    assert len(result.getcolors()) <=16
    custom = apply_operations(source,{**options,'custom_order':True,'order':['depth','border','text']})
    assert custom.mode == 'RGB'
