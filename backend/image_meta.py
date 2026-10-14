"""Remove embedded metadata (EXIF — including GPS position —, XMP, IPTC, text comments) from
uploaded images before they are stored, so a photo can never reveal where or with what device it
was taken. Only metadata containers are dropped; the image data itself is copied byte for byte,
so nothing is re-encoded. Pure functions, no dependencies (unit-tested in tests/test_image_meta.py).

HEIC/HEIF keeps its EXIF inside the ISO-BMFF item structure, which cannot be removed without a
full HEIF library, so `strip_metadata` reports it as unsupported and the upload is refused.
"""
import struct
from typing import Optional


class UnsupportedImage(ValueError):
    pass


def _strip_jpeg(data: bytes) -> bytes:
    """Drop APP1 (EXIF/XMP), APP2-APP15 (except the ICC profile and Adobe APP14) and COM segments."""
    if data[:2] != b"\xff\xd8":
        raise UnsupportedImage("not a JPEG")
    out = bytearray(b"\xff\xd8")
    i = 2
    n = len(data)
    while i < n:
        if data[i] != 0xFF:
            raise UnsupportedImage("corrupt JPEG marker")
        while i < n and data[i] == 0xFF:  # fill bytes
            i += 1
        if i >= n:
            break
        marker = data[i]
        i += 1
        if marker == 0xDA:  # start of scan: the rest is entropy-coded image data, copied as is
            out += b"\xff\xda" + data[i:]
            return bytes(out)
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:  # standalone markers
            out += bytes((0xFF, marker))
            continue
        if marker == 0xD9:
            out += b"\xff\xd9"
            return bytes(out)
        if i + 2 > n:
            raise UnsupportedImage("truncated JPEG segment")
        length = struct.unpack(">H", data[i:i + 2])[0]
        segment = data[i:i + length]
        if len(segment) < length or length < 2:
            raise UnsupportedImage("truncated JPEG segment")
        payload = segment[2:]
        keep = True
        if marker == 0xFE:  # comment
            keep = False
        elif 0xE1 <= marker <= 0xEF:  # APP1..APP15: EXIF, XMP, IPTC (APP13), vendor data
            # Kept: the ICC color profile, and Adobe APP14 (its color-transform flag is needed to
            # decode CMYK/YCCK JPEGs correctly). Neither carries location or device data.
            keep = (marker == 0xE2 and payload.startswith(b"ICC_PROFILE\x00")) or (
                marker == 0xEE and payload.startswith(b"Adobe"))
        if keep:
            out += bytes((0xFF, marker)) + segment
        i += length
    return bytes(out)


_PNG_SIG = b"\x89PNG\r\n\x1a\n"
# Ancillary chunks that carry metadata. Removing whole chunks keeps every other chunk's CRC valid.
_PNG_DROP = {b"eXIf", b"tEXt", b"zTXt", b"iTXt", b"tIME"}


def _strip_png(data: bytes) -> bytes:
    if data[:8] != _PNG_SIG:
        raise UnsupportedImage("not a PNG")
    out = bytearray(_PNG_SIG)
    i = 8
    while i + 8 <= len(data):
        length = struct.unpack(">I", data[i:i + 4])[0]
        ctype = data[i + 4:i + 8]
        end = i + 12 + length
        if end > len(data):
            raise UnsupportedImage("truncated PNG chunk")
        if ctype not in _PNG_DROP:
            out += data[i:end]
        i = end
        if ctype == b"IEND":
            break
    return bytes(out)


def _strip_webp(data: bytes) -> bytes:
    """Drop EXIF and XMP chunks, then clear their flags in VP8X and fix the RIFF size."""
    if data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise UnsupportedImage("not a WebP")
    chunks = bytearray()
    i = 12
    while i + 8 <= len(data):
        ctype = data[i:i + 4]
        size = struct.unpack("<I", data[i + 4:i + 8])[0]
        end = i + 8 + size + (size & 1)  # chunks are padded to an even size
        if i + 8 + size > len(data):
            raise UnsupportedImage("truncated WebP chunk")
        chunk = bytearray(data[i:min(end, len(data))])
        if ctype in (b"EXIF", b"XMP "):
            i = end
            continue
        if ctype == b"VP8X" and size >= 1:
            chunk[8] &= ~(0x08 | 0x04) & 0xFF  # EXIF and XMP presence flags
        chunks += chunk
        i = end
    return b"RIFF" + struct.pack("<I", 4 + len(chunks)) + b"WEBP" + bytes(chunks)


# Byte signatures of every metadata container this module knows (EXIF in JPEG/WebP/HEIC, PNG eXIf
# and text chunks, XMP packets). A file containing none of them has nothing to strip.
_METADATA_SIGNATURES = (b"Exif\x00\x00", b"eXIf", b"EXIF", b"tEXt", b"iTXt", b"zTXt", b"<x:xmpmeta",
                        b"ns.adobe.com/xap", b"XMP ")


def has_metadata_signature(data: bytes) -> bool:
    return any(sig in data for sig in _METADATA_SIGNATURES)


def strip_metadata(data: bytes, ext: str) -> bytes:
    """`data` without embedded metadata. `ext` is the sniffed type ("jpg", "png", "webp", "heic").

    When the structure cannot be walked (HEIC, or a slightly malformed file), the bytes are accepted
    unchanged only if they contain no metadata signature at all; otherwise UnsupportedImage — a
    file is never stored with metadata this module could not remove."""
    try:
        if ext == "jpg":
            return _strip_jpeg(data)
        if ext == "png":
            return _strip_png(data)
        if ext == "webp":
            return _strip_webp(data)
        raise UnsupportedImage(ext)
    except UnsupportedImage:
        if has_metadata_signature(data):
            raise
        return data


def has_location_metadata(data: bytes) -> Optional[bool]:
    """Rough check used by tests: whether an EXIF block is still present (GPS lives inside it)."""
    return b"Exif\x00\x00" in data or b"eXIf" in data
