"""Attach nested EXIF directories without routing them through libtiff.

Pillow/libtiff can reject the nested EXIF/GPS dictionary with an invalid IFD8
offset in a Classic TIFF. Encode pixels first, then append metadata directories
and relink the page chain. Existing compressed strips remain byte-for-byte intact.
Only freshly encoded, temporary Classic TIFF files are accepted here.
"""
from copy import deepcopy
import os
import struct

from PIL import TiffImagePlugin


def append_nested_exif(path, exif, sizes):
    groups = {}
    for tag in (34665, 34853):
        if tag in exif:
            groups[tag] = deepcopy(exif.get_ifd(tag))
    if 34665 in groups and 40965 in groups[34665]:
        groups[34665][40965] = deepcopy(exif.get_ifd(40965))
    if not groups:
        return

    with open(path, 'r+b') as stream:
        head = stream.read(8)
        if head[:4] not in (b'II*\x00', b'MM\x00*'):
            raise ValueError('Nested EXIF requires a Classic TIFF output.')
        endian = '<' if head[:2] == b'II' else '>'
        short = struct.Struct(endian + 'H')
        long = struct.Struct(endian + 'I')
        offset = long.unpack(head[4:])[0]
        link_position = 4
        seen = set()

        for width, height in sizes:
            if not offset or offset in seen:
                raise ValueError('Invalid TIFF page chain while attaching EXIF.')
            seen.add(offset)
            stream.seek(offset)
            count = short.unpack(stream.read(2))[0]
            entries = {}
            for _ in range(count):
                entry = stream.read(12)
                if len(entry) != 12:
                    raise ValueError('Incomplete TIFF directory.')
                entries[short.unpack(entry[:2])[0]] = entry
            next_offset = long.unpack(stream.read(4))[0]

            metadata = TiffImagePlugin.ImageFileDirectory_v2(ifh=head)
            page_groups = deepcopy(groups)
            if 34665 in page_groups:
                for tag, value in ((40962, width), (40963, height)):
                    if tag in page_groups[34665]:
                        page_groups[34665][tag] = value
            for tag, value in page_groups.items():
                metadata[tag] = value

            stream.seek(0, os.SEEK_END)
            if stream.tell() & 1:
                stream.write(b'\0')
            new_offset = stream.tell()
            merged_count = len(set(entries) | set(metadata))
            # Reserve room for the pixel directory entries. The serializer then
            # computes absolute nested offsets for their final on-disk location.
            data = metadata.tobytes(new_offset + 12 * (merged_count - len(metadata)))
            for index in range(len(metadata)):
                entry = data[2 + 12 * index:2 + 12 * (index + 1)]
                entries[short.unpack(entry[:2])[0]] = entry
            auxiliary = data[2 + 12 * len(metadata) + 4:]
            directory = (short.pack(merged_count) + b''.join(entries[tag] for tag in sorted(entries))
                         + long.pack(next_offset) + auxiliary)
            if new_offset + len(directory) >= 2**32:
                raise ValueError('TIFF plus EXIF exceeds the Classic TIFF size limit.')
            stream.write(directory)
            stream.seek(link_position)
            stream.write(long.pack(new_offset))
            link_position = new_offset + 2 + 12 * merged_count
            offset = next_offset
        if offset:
            raise ValueError('TIFF contains more pages than the encoded document.')
