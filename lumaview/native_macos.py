"""Small native macOS services, imported lazily by the application."""
from __future__ import annotations

import io
import sys

from PIL import Image


def recognize_text(image: Image.Image, languages=None) -> str:
    """Read text locally with Apple's accurate Vision recognizer.

    ``languages`` accepts BCP-47 identifiers such as ``en-US`` and ``it-IT``.
    An omitted list lets Vision use its default recognition languages. No image
    data leaves the Mac. Unsupported systems and failures raise RuntimeError.
    """
    if sys.platform != 'darwin':
        raise RuntimeError('OCR requires macOS.')
    try:
        from Foundation import NSData
        import Quartz
        import Vision
    except ImportError as exc:
        raise RuntimeError('OCR dependencies are missing. Run Install.command again.') from exc
    rgba = image.convert('RGBA')
    flattened = Image.new('RGBA', image.size, 'white')
    flattened.alpha_composite(rgba)
    encoded = io.BytesIO()
    flattened.convert('RGB').save(encoded, 'PNG')
    data = encoded.getvalue()
    native_data = NSData.dataWithBytes_length_(data, len(data))
    source = Quartz.CGImageSourceCreateWithData(native_data, None)
    if source is None:
        raise RuntimeError('Could not prepare the image for text recognition.')
    native_image = Quartz.CGImageSourceCreateImageAtIndex(source, 0, None)
    if native_image is None:
        raise RuntimeError('Could not prepare the image for text recognition.')
    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    request.setUsesLanguageCorrection_(True)
    if languages:
        request.setRecognitionLanguages_(list(languages))
    if hasattr(request, 'setAutomaticallyDetectsLanguage_'):
        request.setAutomaticallyDetectsLanguage_(True)
    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(native_image, {})
    success, error = handler.performRequests_error_([request], None)
    if not success:
        reason = str(error.localizedDescription()) if error else 'Unknown Vision error.'
        raise RuntimeError(f'Text recognition failed: {reason}')
    # Vision supplies recognized text observations in reading order.
    lines = []
    for observation in request.results() or []:
        candidates = observation.topCandidates_(1)
        if candidates:
            lines.append(str(candidates[0].string()))
    return '\n'.join(lines)
