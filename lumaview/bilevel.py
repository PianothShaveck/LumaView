"""Adaptive two-colour quantization for monochrome TIFF.

The colour histogram, palette and bounded error diffusion are independent of
filenames, reference images and output byte counts. All calculations use integer
arithmetic so their result is the same on Apple Silicon and other architectures.
"""
from __future__ import annotations

import sys
import numpy as np
from numba import njit
from PIL import Image


# Frozen releases compile in memory; source installations keep their native code
# cache beside the installed source. No compiler or developer tools are required.
_compile = njit(cache=not getattr(sys, 'frozen', False), nogil=True)


@_compile
def _histogram(rgb):
    histogram = np.zeros(128 * 128 * 128, dtype=np.uint16)
    height, width, _ = rgb.shape
    for y in range(height):
        for x in range(width):
            index = ((int(rgb[y, x, 2]) >> 1) << 14
                     | (int(rgb[y, x, 1]) >> 1) << 7
                     | (int(rgb[y, x, 0]) >> 1))
            if histogram[index] < 65535:
                histogram[index] += 1
    return histogram


def _palette(rgb):
    histogram = _histogram(rgb)
    indices = np.flatnonzero(histogram)
    count = histogram[indices].astype(np.int64)
    colors = np.column_stack((indices >> 14, (indices >> 7) & 127, indices & 127))
    low, high = colors.min(axis=0), colors.max(axis=0)
    axis = int(np.argmax((high - low) * (2, 3, 1)))
    split = (int(low[axis]) + int(high[axis])) // 2
    upper = colors[:, axis] > split
    centers = colors * 2 + 1
    palette = np.empty((2, 3), dtype=np.int64)
    if not upper.any():
        # There is only one occupied colour cell: retain its ordinary black or
        # white interpretation without inventing an empty palette entry.
        return None
    for i, selected in enumerate((~upper, upper)):
        total = count[selected].sum()
        sums = (centers[selected] * count[selected, None]).sum(axis=0)
        palette[i] = (sums + total // 2) // total
    return palette


@_compile
def _diffuse(rgb, palette):
    height, width, _ = rgb.shape
    output = np.empty((height, width), dtype=np.bool_)
    current = np.zeros((width + 2, 3), dtype=np.int32)
    following = np.zeros((width + 2, 3), dtype=np.int32)
    values = np.empty(3, dtype=np.int64)
    weights = (4, 9, 1)
    for row in range(height):
        y = height - 1 - row
        direction = 1 if row % 2 == 0 else -1
        for column in range(width):
            x = column if direction == 1 else width - 1 - column
            slot = x + 1
            distance0, distance1 = 0, 0
            for channel in range(3):
                correction = (int(current[slot, channel]) + 8) >> 4
                magnitude = abs(correction)
                if magnitude >= 48:
                    magnitude = 32
                elif magnitude >= 16:
                    magnitude = 8 + magnitude // 2
                if correction < 0:
                    magnitude = -magnitude
                value = min(255, max(0, int(rgb[y, x, 2 - channel]) + magnitude))
                values[channel] = value
                center = (value & 254) + 1
                distance0 += (center - palette[0, channel]) ** 2 * weights[channel]
                distance1 += (center - palette[1, channel]) ** 2 * weights[channel]
            chosen = 1 if distance1 < distance0 else 0
            output[y, x] = chosen == 1
            for channel in range(3):
                error = values[channel] - palette[chosen, channel]
                current[slot + direction, channel] += error * 7
                following[slot - direction, channel] += error * 3
                following[slot, channel] += error * 5
                following[slot + direction, channel] += error
        current, following = following, current
        following[:] = 0
    return output


def adaptive_bilevel(image: Image.Image) -> Image.Image:
    """Return 1-bit pixels without resizing; RGB values are used as supplied.

    The caller controls alpha compositing. Existing 1-bit images are preserved.
    """
    if image.mode == '1':
        return image.copy()
    rgb = np.asarray(image.convert('RGB'), dtype=np.uint8)
    palette = _palette(rgb)
    if palette is None:
        return image.convert('L').point([0] * 128 + [255] * 128, mode='1')
    return Image.fromarray(_diffuse(rgb, palette))


def warm_up():
    """Compile both kernels before replacing a working source installation."""
    pixels = np.zeros((2, 2, 3), dtype=np.uint8)
    pixels[1] = 255
    result = adaptive_bilevel(Image.fromarray(pixels))
    if result.mode != '1' or result.getpixel((0, 0)) != 0 or result.getpixel((0, 1)) != 255:
        raise RuntimeError('The native monochrome converter failed its installation check.')
