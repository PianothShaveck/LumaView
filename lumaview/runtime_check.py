"""Exercise bundled codecs during release startup checks."""
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
from PIL import Image

from lumaview.bilevel import adaptive_bilevel
from lumaview.image_io import load_document, save_document
from lumaview.model import EXTENSIONS, ImageDocument


def verify_codecs():
    # Tiny files still load the actual native encoders/decoders in a frozen app.
    y, x = np.indices((48, 64))
    pixels = np.stack((x * 3 % 256, y * 5 % 256, (x + y) * 2 % 256), axis=-1).astype('uint8')
    original = Image.fromarray(pixels)
    document = ImageDocument([original])
    with TemporaryDirectory(prefix='lumaview-codecs-') as temporary:
        directory = Path(temporary)
        for fmt in ('JPEG', 'PNG', 'TIFF', 'PDF', 'GIF', 'WEBP', 'JPEG2000', 'AVIF', 'HEIF', 'JXL', 'JPEGLS'):
            target = directory / ('sample' + EXTENSIONS[fmt])
            options = {'pdf_dpi': 96}
            if fmt == 'TIFF':
                options['tiff_compression'] = 'group4'
            save_document(document, target, fmt, options)
            decoded = load_document(target).frames[0]
            if decoded.size != original.size:
                raise RuntimeError(f'{fmt}: codec startup check changed image dimensions.')
            if fmt == 'PNG' and not np.array_equal(np.asarray(decoded.convert('RGB')), pixels):
                raise RuntimeError('PNG: lossless codec startup check failed.')
            if fmt == 'TIFF' and not np.array_equal(np.asarray(decoded), np.asarray(adaptive_bilevel(original))):
                raise RuntimeError('TIFF: native monochrome startup check failed.')
