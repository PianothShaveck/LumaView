"""Shared image, processing and encoding contracts."""
from dataclasses import dataclass, field
from pathlib import Path
from copy import deepcopy
from typing import Any
from PIL import Image

@dataclass
class ImageDocument:
    frames: list[Image.Image]
    source: Path | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    durations: list[int] = field(default_factory=list)
    loop: int = 0
    dpi: tuple[float, float] = (72.0, 72.0)
    def clone(self):
        return ImageDocument([im.copy() for im in self.frames], self.source,
            deepcopy(self.metadata), list(self.durations), self.loop, self.dpi)

FORMAT_LABELS = {
 'JPEG':'JPG - JPEG', 'PNG':'PNG - Portable Network Graphics',
 'TIFF':'TIF - Tagged Image File Format', 'PDF':'PDF - Portable Document Format',
 'GIF':'GIF - Graphics Interchange Format', 'WEBP':'WEBP - WebP',
 'BMP':'BMP - Windows Bitmap', 'ICO':'ICO - Windows Icon',
 'JPEG2000':'JP2 - JPEG 2000', 'AVIF':'AVIF - AV1 Image File',
 'HEIF':'HEIC - High Efficiency Image', 'JXL':'JXL - JPEG XL', 'JPEGLS':'JLS - JPEG-LS',
 'PCX':'PCX - Zsoft Paintbrush', 'PBM':'PBM - Portable Bitmap',
 'PGM':'PGM - Portable Graymap', 'PPM':'PPM - Portable Pixmap',
 'QOI':'QOI - Quite OK Image', 'TGA':'TGA - Truevision Targa',
 'DDS':'DDS - DirectDraw Surface', 'EPS':'EPS - Encapsulated PostScript',
 'RAW':'RAW - Headerless pixel data',
}
EXTENSIONS = {'JPEG':'.jpg','PNG':'.png','TIFF':'.tif','PDF':'.pdf','GIF':'.gif','WEBP':'.webp','BMP':'.bmp','ICO':'.ico','JPEG2000':'.jp2','AVIF':'.avif','HEIF':'.heic','JXL':'.jxl','JPEGLS':'.jls','PCX':'.pcx','PBM':'.pbm','PGM':'.pgm','PPM':'.ppm','QOI':'.qoi','TGA':'.tga','DDS':'.dds','EPS':'.eps','RAW':'.raw'}
READ_EXTENSIONS = {'.jpg','.jpeg','.jpe','.png','.apng','.tif','.tiff','.pdf','.gif','.webp','.bmp','.dib','.ico','.icns','.jp2','.j2k','.jpf','.avif','.heif','.heic','.jxl','.jls','.pcx','.pbm','.pgm','.ppm','.pnm','.qoi','.tga','.dds','.eps','.psd','.psb','.svg','.svgz','.hdr','.exr','.im','.sgi','.rgb','.rgba','.ras','.xbm','.xpm','.fit','.fits','.dng','.cr2','.cr3','.nef','.arw','.orf','.rw2','.raf','.pef'}
OPERATIONS = ['crop','resize','auto_crop','rotate','flip','fine_rotation','canvas','grayscale','negative','auto_colors','corrections','replace_color','swap_channels','effects','sharpen','blur','border','text','watermark','depth']

def default_input():
    return {'pdf_dpi':96, 'svg_width':1024, 'svg_height':800, 'svg_keep_aspect':False}

def default_advanced():
    return {
     'crop':False,'crop_x':0,'crop_y':0,'crop_width':0,'crop_height':0,'crop_anchor':'top-left',
     'resize':False,'resize_mode':'dimensions','width':0.0,'height':0.0,'units':'pixels','long_side':1920.0,'short_side':1080.0,'megapixels':2.0,'percent_width':100.0,'percent_height':100.0,
     'preserve_aspect':True,'resample':True,'resample_filter':'Lanczos','size_bound':'fit','dpi_only':False,'no_enlarge':False,'no_shrink':False,'new_dpi':0.0,
     'depth':False,'depth_mode':'24','custom_colors':256,'dither':True,'best_palette':True,
     'auto_colors':False,'flip_horizontal':False,'flip_vertical':False,'rotate_left':False,'rotate_right':False,'grayscale':False,'negative':False,
     'auto_crop':False,'auto_crop_tolerance':0,'canvas':False,'canvas_width':0,'canvas_height':0,'canvas_relative':False,'canvas_anchor':'center','canvas_color':'#ffffff',
     'text':False,'text_value':'$F','text_font':'','text_size':32,'text_color':'#ffffff','text_background':'','text_opacity':100,'text_anchor':'bottom-right','text_x':20,'text_y':20,
     'watermark':False,'watermark_path':'','watermark_opacity':50,'watermark_scale':100,'watermark_anchor':'bottom-right','watermark_x':20,'watermark_y':20,
     'replace_color':False,'replace_from':'#ffffff','replace_to':'#000000','replace_tolerance':0,
     'border':False,'border_left':10,'border_top':10,'border_right':10,'border_bottom':10,'border_color':'#000000',
     'swap_channels':'RGB','sharpen':False,'sharpen_value':20,'brightness':False,'brightness_value':0,'contrast':False,'contrast_value':0,'gamma':False,'gamma_value':1.0,'saturation':False,'saturation_value':0,
     'balance_r':False,'balance_r_value':0,'balance_g':False,'balance_g_value':0,'balance_b':False,'balance_b_value':0,'blur':False,'blur_value':1,
     'fine_rotation':False,'rotation_angle':0.0,'rotation_expand':True,'rotation_color':'#ffffff',
     'effects':False,'effect':'emboss','effect_value':1.0,
     'overwrite':False,'delete_originals':False,'create_subfolders':False,'keep_datetime':False,'all_pages':True,'custom_order':False,'order':list(OPERATIONS),
    }

def default_format(fmt='PNG'):
    return {'quality':90,'progressive':False,'grayscale':False,'no_subsampling':False,
     'keep_exif':True,'keep_iptc':True,'keep_comment':True,'keep_xmp':True,'keep_icc':True,'original_quality':False,'reset_orientation':True,'preview':False,'target_kb':0.0,
     'compression':6,'custom_transparency':False,'transparent_color':'#ffffff','transparency_alpha':True,'transparency_mode':'color','transparency_index':0,
     'optimize_png':False,'optipng_level':2,'pnm_ascii':False,'gif_interlace':False,
     'tiff_compression':'tiff_lzw','all_pages':True,'tiff_gray_palette':True,
     'tiff_bilevel_method':'adaptive','tiff_threshold':128,'tiff_bilevel_contrast':3.0,
     'tiff_adaptive_alpha_background':False,
     'pdf_compression':'jpeg','pdf_quality':90,'pdf_dpi':144,'pdf_page_size':'image','pdf_landscape':False,'pdf_fit':True,'pdf_title':'','pdf_author':'','pdf_password':'',
     'lossless':False,'ico_sizes':[16,24,32,48,64,128,256],'raw_mode':'RGB','background':'#ffffff',
    }
