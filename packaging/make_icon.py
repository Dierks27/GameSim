"""Draw GridSim's icon and write packaging/gridsim.ico (needs Pillow; run only when the drawing changes).

    python packaging/make_icon.py                    # rewrites packaging/gridsim.ico
    python packaging/make_icon.py --preview x.png    # also saves a sheet of all sizes to look at

The picture: a dark rounded square holding a green football field with white yard lines; from 32 pixels up, a gold
arc with a ball at its end (one simulated play). Every size is drawn on its own (line widths are chosen per size)
at several times its size and then scaled down, so the small ones stay crisp.

The file is a real multi-size .ico: 16, 24, 32, 48, 64 and 128 pixels as 32-bit bitmaps and 256 pixels as PNG, the
layout Windows icon tools produce. The installer (SetupIconFile, the shortcuts) and the window both read it.
"""
from __future__ import annotations

import io
import struct
import sys
from pathlib import Path

from PIL import Image, ImageDraw

SIZES = (16, 24, 32, 48, 64, 128, 256)
DARK = (17, 22, 28, 255)          # the square
EDGE = (46, 56, 68, 255)          # a thin lighter rim, from 48 pixels up
GREEN = (31, 157, 85, 255)        # the field
GREEN_DARK = (24, 128, 69, 255)   # every other 10-yard band, from 48 pixels up
WHITE = (255, 255, 255, 255)
GOLD = (255, 205, 60, 255)
# the yard lines of the three smallest sizes, placed by hand on whole pixels: (left edge, width) inside the icon
SMALL_LINES = {16: [(4, 1), (7, 2), (11, 1)], 24: [(6, 2), (11, 2), (16, 2)], 32: [(8, 1), (13, 1), (18, 1), (23, 1)]}


def draw(size: int) -> Image.Image:
    k = 4 if size >= 128 else 8                      # oversampling
    n = size * k
    im = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)

    def px(v: float) -> int:                         # final-size pixels -> drawing units
        return int(round(v * k))

    d.rounded_rectangle([0, 0, n - 1, n - 1], radius=px(size * 0.22), fill=DARK)
    if size >= 48:
        d.rounded_rectangle([0, 0, n - 1, n - 1], radius=px(size * 0.22), outline=EDGE, width=px(max(1, size / 64)))

    # the field: whole pixels at the small sizes, so its edges do not blur
    inset = {16: 2, 24: 3, 32: 4}.get(size, round(size * 0.125))
    x0, y0, x1, y1 = px(inset), px(inset), px(size - inset), px(size - inset)
    d.rounded_rectangle([x0, y0, x1 - 1, y1 - 1], radius=px(max(1, size * 0.06)), fill=GREEN)

    lines = 3 if size <= 24 else 5
    width = size - 2 * inset
    lw = {16: 1, 24: 1, 32: 1, 48: 2, 64: 2, 128: 4, 256: 7}[size]
    if size >= 48:                                   # darker bands between every other pair of yard lines
        for i in range(0, lines + 1, 2):
            bx0 = inset + width * i / (lines + 1)
            bx1 = inset + width * (i + 1) / (lines + 1)
            d.rectangle([px(bx0), y0, px(bx1) - 1, y1 - 1], fill=GREEN_DARK)
        d.rounded_rectangle([x0, y0, x1 - 1, y1 - 1], radius=px(max(1, size * 0.06)), outline=GREEN, width=px(lw / 2))
    if size in SMALL_LINES:                          # whole-pixel yard lines, evenly spaced: (left edge, width)
        marks = SMALL_LINES[size]
    else:
        marks = [(inset + width * i / (lines + 1) - lw / 2, lw) for i in range(1, lines + 1)]
    for left, w in marks:
        d.rectangle([px(left), y0 + px(lw if size >= 48 else 0), px(left + w) - 1,
                     y1 - 1 - px(lw if size >= 48 else 0)], fill=WHITE)

    if size >= 32:                                   # one simulated play: a gold arc and the ball at its end
        aw = {32: 2, 48: 3, 64: 4, 128: 7, 256: 13}[size]
        ax0, ay0 = inset + width * 0.10, size - inset - width * 0.16
        ax1, ay1 = inset + width * 0.80, inset + width * 0.34
        pts = []
        for t in range(0, 41):
            u = t / 40
            x = ax0 + (ax1 - ax0) * u
            y = ay0 + (ay1 - ay0) * u - width * 0.30 * 4 * u * (1 - u) * 0.55
            pts.append((px(x), px(y)))
        d.line(pts, fill=GOLD, width=px(aw), joint="curve")
        r = aw * 1.15
        bx, by = pts[-1]
        d.ellipse([bx - px(r), by - px(r), bx + px(r), by + px(r)], fill=GOLD)
        sx, sy = pts[0]
        d.ellipse([sx - px(aw / 2), sy - px(aw / 2), sx + px(aw / 2), sy + px(aw / 2)], fill=GOLD)

    return im.resize((size, size), Image.LANCZOS)


def _bmp_entry(im: Image.Image) -> bytes:
    """One icon image as a 32-bit bitmap: header, the pixels bottom-up as BGRA, then the 1-bit transparency mask."""
    w, h = im.size
    rgba = im.tobytes("raw", "BGRA")
    rows = [rgba[y * w * 4:(y + 1) * w * 4] for y in range(h)]
    xor = b"".join(reversed(rows))
    stride = ((w + 31) // 32) * 4
    alpha = im.getchannel("A").tobytes()
    mask_rows = []
    for y in range(h):
        bits = bytearray(stride)
        for x in range(w):
            if alpha[y * w + x] == 0:
                bits[x // 8] |= 0x80 >> (x % 8)
        mask_rows.append(bytes(bits))
    mask = b"".join(reversed(mask_rows))
    header = struct.pack("<IiiHHIIiiII", 40, w, h * 2, 1, 32, 0, len(xor) + len(mask), 0, 0, 0, 0)
    return header + xor + mask


def _png_entry(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def build_ico(images: dict[int, Image.Image]) -> bytes:
    entries = [(s, _png_entry(im) if s >= 256 else _bmp_entry(im)) for s, im in sorted(images.items())]
    out = struct.pack("<HHH", 0, 1, len(entries))
    offset = 6 + 16 * len(entries)
    for s, data in entries:
        out += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(data), offset)   # 256 is written as 0
        offset += len(data)
    return out + b"".join(data for _, data in entries)


def main(argv: list[str]) -> int:
    images = {s: draw(s) for s in SIZES}
    target = Path(__file__).resolve().parent / "gridsim.ico"
    target.write_bytes(build_ico(images))
    print(f"wrote {target} ({target.stat().st_size} bytes, sizes {', '.join(map(str, SIZES))})")
    if "--preview" in argv:
        out = Path(argv[argv.index("--preview") + 1])
        pad = 12
        sheet = Image.new("RGBA", (sum(SIZES) + pad * (len(SIZES) + 1), 2 * (max(SIZES) + 2 * pad)), (240, 240, 240, 255))
        x = pad
        for s in SIZES:
            sheet.alpha_composite(images[s], (x, pad))
            x += s + pad
        x = pad
        for s in SIZES:                              # second row: every size blown up to 96 px, to judge the small ones
            big = images[s].resize((96, 96), Image.NEAREST)
            sheet.paste((30, 30, 30, 255), (x - 2, max(SIZES) + 2 * pad - 2, x + 98, max(SIZES) + 2 * pad + 98))
            sheet.alpha_composite(big, (x, max(SIZES) + 2 * pad))
            x += 96 + pad
        sheet.save(out)
        print(f"preview: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
