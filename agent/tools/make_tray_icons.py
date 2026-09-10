"""Generate the tray status icons into agent/icons/. Stdlib only.

Run with any Python 3:  python agent/tools/make_tray_icons.py

Four states, because "status at a glance" is a stated requirement and a taskbar
gives you about 16 pixels to say it in:

    ready.ico   green   enrolled, online, nothing to do
    busy.ico    blue    a run is in progress
    warn.ico    amber   needs attention — sign-in lost, or the last run failed
    off.ico     grey    not connected, offline, paused or disabled

Deliberately NOT the ribbon button's dark indigo: the Windows taskbar is dark by
default, where indigo-on-dark is invisible. Each icon is a filled disc in the
status colour with a white sync glyph knocked out of it, so the colour carries
the meaning and survives being shrunk to 16px while the glyph says which app it
is.

Each .ico embeds PNGs at 16 and 32 px (PNG-in-ICO, fine on Win7+).
"""

import math
import os
import struct
import zlib

SS = 8  # supersample factor

COLOURS = {
    "ready": (0x2E, 0x9E, 0x5B),
    "busy":  (0x3B, 0x82, 0xF6),
    "warn":  (0xE0, 0xA1, 0x00),
    "off":   (0x8A, 0x8F, 0x98),
}

SIZES = (16, 32)


def _tri_contains(px, py, ax, ay, bx, by, cx, cy):
    d1 = (bx - ax) * (py - ay) - (by - ay) * (px - ax)
    d2 = (cx - bx) * (py - by) - (cy - by) * (px - bx)
    d3 = (ax - cx) * (py - cy) - (ay - cy) * (px - cx)
    neg = d1 < 0 or d2 < 0 or d3 < 0
    pos = d1 > 0 or d2 > 0 or d3 > 0
    return not (neg and pos)


def _glyph_shapes(n):
    """Arc band + arrowheads + caps, in an n-unit square."""
    cx = cy = n / 2.0
    radius = n * 0.27
    stroke = max(1.6, n * 0.105)
    arcs = [(36.0, 188.0), (216.0, 368.0)]
    arrow_len = 2.15 * stroke
    arrow_half = 1.62 * stroke
    heads, caps = [], []
    for start, end in arcs:
        ts = math.radians(start)
        px = cx + radius * math.cos(ts)
        py = cy + radius * math.sin(ts)
        dx, dy = math.sin(ts), -math.cos(ts)
        nx, ny = math.cos(ts), math.sin(ts)
        heads.append(((px + dx * arrow_len, py + dy * arrow_len),
                      (px + nx * arrow_half, py + ny * arrow_half),
                      (px - nx * arrow_half, py - ny * arrow_half)))
        te = math.radians(end)
        caps.append((cx + radius * math.cos(te), cy + radius * math.sin(te)))
    return cx, cy, radius, stroke, arcs, heads, caps


def _in_arc(theta, start, end):
    t = theta % 360.0
    s, e = start % 360.0, end % 360.0
    return (s <= t <= e) if s <= e else (t >= s or t <= e)


def render(n, rgb):
    cx, cy, radius, stroke, arcs, heads, caps = _glyph_shapes(n)
    disc_r = n * 0.47
    big = n * SS
    step = 1.0 / SS
    # Per final pixel: (disc coverage, glyph coverage)
    disc = [0] * (n * n)
    glyph = [0] * (n * n)

    for by in range(big):
        y = (by + 0.5) * step
        row = (by // SS) * n
        for bx in range(big):
            x = (bx + 0.5) * step
            dx, dy = x - cx, y - cy
            dist = math.hypot(dx, dy)
            if dist > disc_r:
                continue
            idx = row + (bx // SS)
            disc[idx] += 1

            hit = False
            if abs(dist - radius) <= stroke / 2.0:
                theta = math.degrees(math.atan2(dy, dx))
                for start, end in arcs:
                    if _in_arc(theta, start, end):
                        hit = True
                        break
            if not hit:
                for capx, capy in caps:
                    if math.hypot(x - capx, y - capy) <= stroke / 2.0:
                        hit = True
                        break
            if not hit:
                for tip, b, c in heads:
                    if _tri_contains(x, y, tip[0], tip[1], b[0], b[1],
                                     c[0], c[1]):
                        hit = True
                        break
            if hit:
                glyph[idx] += 1

    total = float(SS * SS)
    out = bytearray()
    for i in range(n * n):
        alpha = disc[i] / total
        mark = glyph[i] / total
        # White glyph composited over the status colour, both inside the disc.
        r = rgb[0] * (1 - mark) + 255 * mark
        g = rgb[1] * (1 - mark) + 255 * mark
        b = rgb[2] * (1 - mark) + 255 * mark
        out += bytes((int(round(r)), int(round(g)), int(round(b)),
                      int(round(alpha * 255))))
    return bytes(out)


def png_bytes(n, rgba):
    raw = bytearray()
    for y in range(n):
        raw.append(0)
        raw += rgba[y * n * 4:(y + 1) * n * 4]

    def chunk(ctype, body):
        head = struct.pack(">I", len(body)) + ctype + body
        return head + struct.pack(">I", zlib.crc32(ctype + body) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", n, n, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + chunk(b"IEND", b""))


def write_ico(path, images):
    """images: list of (size, png_bytes)."""
    count = len(images)
    header = struct.pack("<HHH", 0, 1, count)
    offset = 6 + 16 * count
    entries, blobs = b"", b""
    for size, blob in images:
        dim = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32,
                               len(blob), offset)
        blobs += blob
        offset += len(blob)
    with open(path, "wb") as handle:
        handle.write(header + entries + blobs)


def main():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_dir = os.path.join(here, "icons")
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    for name, rgb in COLOURS.items():
        images = [(n, png_bytes(n, render(n, rgb))) for n in SIZES]
        target = os.path.join(out_dir, "%s.ico" % name)
        write_ico(target, images)
        print("wrote %s (%s)" % (target, " + ".join("%dpx" % n for n in SIZES)))


if __name__ == "__main__":
    main()
