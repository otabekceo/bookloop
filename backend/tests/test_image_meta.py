"""Uploaded photos lose their embedded metadata (EXIF incl. GPS, XMP, comments) and nothing else.
Images are produced by Pillow (a test-only tool here) with a GPS position, the way phone cameras do."""
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from image_meta import UnsupportedImage, has_location_metadata, strip_metadata  # noqa: E402

Image = pytest.importorskip("PIL.Image")

GPS_IFD = 0x8825


def _exif_with_gps():
    exif = Image.Exif()
    exif[0x010F] = "PhoneMaker"  # Make
    exif[0x0110] = "PhoneModel 12"  # Model
    exif[0x0131] = "Camera 1.0"  # Software
    exif.get_ifd(GPS_IFD).update({1: "N", 2: (41.0, 18.0, 40.0), 3: "E", 4: (69.0, 16.0, 47.0)})  # Tashkent
    return exif


def _image():
    img = Image.new("RGB", (64, 48))
    img.putdata([((x * 4) % 256, (y * 5) % 256, (x * y) % 256) for y in range(48) for x in range(64)])
    return img


def _encode(fmt, **kw):
    buf = io.BytesIO()
    _image().save(buf, fmt, **kw)
    return buf.getvalue()


def _pixels(data):
    return list(Image.open(io.BytesIO(data)).convert("RGB").getdata())


def test_jpeg_gps_exif_xmp_and_comments_removed_pixels_untouched():
    raw = _encode("JPEG", quality=90, exif=_exif_with_gps().tobytes(), comment=b"secret note",
                  xmp=b"<x:xmpmeta><rdf:Description exif:GPSLatitude='41,18N'/></x:xmpmeta>")
    assert has_location_metadata(raw) and b"PhoneModel" in raw and b"secret note" in raw and b"GPSLatitude" in raw
    clean = strip_metadata(raw, "jpg")
    for leak in (b"Exif\x00\x00", b"PhoneModel", b"PhoneMaker", b"secret note", b"GPSLatitude", b"xmpmeta"):
        assert leak not in clean, leak
    assert not has_location_metadata(clean)
    assert _pixels(clean) == _pixels(raw)  # same compressed image data, decoded identically
    assert not Image.open(io.BytesIO(clean)).getexif()


def test_jpeg_keeps_color_profile():
    icc = b"\x00" * 128
    raw = _encode("JPEG", icc_profile=icc, exif=_exif_with_gps().tobytes())
    clean = strip_metadata(raw, "jpg")
    assert Image.open(io.BytesIO(clean)).info.get("icc_profile") == icc
    assert not has_location_metadata(clean)


def test_png_exif_and_text_chunks_removed():
    from PIL import PngImagePlugin
    info = PngImagePlugin.PngInfo()
    info.add_text("Comment", "taken at home")
    info.add_itxt("Location", "41.31,69.28")
    raw = _encode("PNG", exif=_exif_with_gps().tobytes(), pnginfo=info)
    assert b"eXIf" in raw and b"taken at home" in raw
    clean = strip_metadata(raw, "png")
    for leak in (b"eXIf", b"tEXt", b"iTXt", b"taken at home", b"41.31,69.28", b"PhoneModel"):
        assert leak not in clean, leak
    assert _pixels(clean) == _pixels(raw)


def test_webp_exif_and_xmp_removed():
    raw = _encode("WEBP", exif=_exif_with_gps().tobytes(), xmp=b"<x:xmpmeta>gps</x:xmpmeta>", lossless=True)
    assert b"EXIF" in raw and b"PhoneModel" in raw
    clean = strip_metadata(raw, "webp")
    assert b"EXIF" not in clean and b"PhoneModel" not in clean and b"xmpmeta" not in clean
    assert _pixels(clean) == _pixels(raw)
    assert int.from_bytes(clean[4:8], "little") == len(clean) - 8  # RIFF size fixed up


def test_clean_images_pass_through_unchanged_in_content():
    for fmt, ext in (("JPEG", "jpg"), ("PNG", "png")):
        raw = _encode(fmt)
        assert _pixels(strip_metadata(raw, ext)) == _pixels(raw)


HEIC = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 64


def test_unparseable_files_with_metadata_are_refused():
    with pytest.raises(UnsupportedImage):  # HEIC's EXIF cannot be removed here
        strip_metadata(HEIC + b"Exif\x00\x00MM\x00*GPS", "heic")
    with pytest.raises(UnsupportedImage):  # truncated JPEG that still carries EXIF
        strip_metadata(b"\xff\xd8\xff\xe1\x00\x10Exif\x00\x00", "jpg")
    with pytest.raises(UnsupportedImage):  # malformed PNG with a text chunk
        strip_metadata(b"\x89PNG\r\n\x1a\n\x00\x00\x00\x09tEXtsecret", "png")


def test_unparseable_files_without_any_metadata_pass_unchanged():
    assert strip_metadata(HEIC, "heic") == HEIC
    odd_png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIDATx\x9c"  # malformed, but there is nothing to leak
    assert strip_metadata(odd_png, "png") == odd_png
