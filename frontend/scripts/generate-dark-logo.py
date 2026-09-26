"""Generates dark-mode variants of the BookLoop logo assets by recoloring the existing flat two-tone
artwork (near-black ink + green loop accent, RGBA, transparent background) — NOT by redesigning it.
Alpha (and therefore geometry/anti-aliasing) is preserved exactly; only RGB is remapped, classified by
hue (green loop vs. near-black/gray ink). Run once from frontend/: python scripts/generate-dark-logo.py
"""
from PIL import Image

INK_DARK = (245, 246, 243)  # #F5F6F3 — Primary Text (dark mode)
LOOP_DARK = (78, 158, 118)  # #4E9E76 — Primary Brand Green (dark mode)

SOURCES = ["bookloop-logo.png", "bookloop-wordmark.png"]


def is_greenish(r, g, b):
    return g > r + 15 and g > b + 15


def recolor(path_in, path_out):
    im = Image.open(path_in).convert("RGBA")
    px = im.load()
    w, h = im.size
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a == 0:
                continue
            target = LOOP_DARK if is_greenish(r, g, b) else INK_DARK
            px[x, y] = (target[0], target[1], target[2], a)
    im.save(path_out)
    print(f"wrote {path_out}")


if __name__ == "__main__":
    for name in SOURCES:
        base, ext = name.rsplit(".", 1)
        recolor(f"assets/images/{name}", f"assets/images/{base}-dark.{ext}")
