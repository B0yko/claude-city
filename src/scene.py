"""Claude Parachute: a pixel-art city at dusk. A helicopter flies in, Claude jumps, opens a parachute, lands on a
roof, packs the canopy, takes the stairs down and leaves in a taxi. Then the helicopter comes around again.

Every pixel is a pure function of the loop time t. Periodic things (stars, clouds, traffic, lights, water, palms)
complete a whole number of cycles per LOOP, and the story starts and ends off screen, so frame(LOOP) == frame(0)
and the video loops without a seam (test_scene.py checks this).

The art lives on a small grid (ART_W x ART_H = 864x559, the 16" MacBook Pro panel 3456x2234 divided by 4) and is
scaled up by an integer factor with nearest-neighbour sampling, so the pixels stay crisp.
"""
from __future__ import annotations

import math
import random
from functools import lru_cache

import numpy as np

LOOP = 60.0
ART_W, ART_H = 864, 559


# ============================================================================================ math helpers
def clamp(v, lo=0.0, hi=1.0):
    return lo if v < lo else hi if v > hi else v


def lerp(a, b, u):
    return a + (b - a) * u


def span(t, t0, t1):
    return clamp((t - t0) / (t1 - t0))


def smooth(u):
    u = clamp(u)
    return u * u * (3 - 2 * u)


def ease_out(u):
    u = clamp(u)
    return 1 - (1 - u) ** 3


def cyc(t, n, phase=0.0):
    """Phase in [0, 1) of something that repeats n times per loop. Integer n keeps the loop seamless."""
    return (t * n / LOOP + phase) % 1.0


def wave(t, n, phase=0.0):
    return math.sin(2 * math.pi * (t * n / LOOP + phase))


def hermite(t, k0, k1):
    t0, x0, y0, vx0, vy0 = k0
    t1, x1, y1, vx1, vy1 = k1
    h = t1 - t0
    u = (t - t0) / h
    h00, h10, h01, h11 = 2 * u**3 - 3 * u**2 + 1, u**3 - 2 * u**2 + u, -2 * u**3 + 3 * u**2, u**3 - u**2
    return (h00 * x0 + h10 * h * vx0 + h01 * x1 + h11 * h * vx1,
            h00 * y0 + h10 * h * vy0 + h01 * y1 + h11 * h * vy1)


def path(t, keys):
    """Piecewise cubic Hermite through keys [(t, x, y, vx, vy), ...]: position and velocity stay continuous."""
    if t <= keys[0][0]:
        return keys[0][1], keys[0][2]
    for a, b in zip(keys, keys[1:]):
        if t <= b[0]:
            return hermite(t, a, b)
    return keys[-1][1], keys[-1][2]


def hexc(s):
    s = s.lstrip("#")
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)


def mix(a, b, u):
    return tuple(int(round(lerp(x, y, u))) for x, y in zip(a, b))


def shade(c, k):
    return tuple(int(clamp(v * k, 0, 255)) for v in c)


BAYER4 = (np.array([[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]], np.float32) + 0.5) / 16


def bayer(h, w, ox=0, oy=0):
    tile = np.tile(BAYER4, (h // 4 + 2, w // 4 + 2))
    return tile[oy % 4:oy % 4 + h, ox % 4:ox % 4 + w]


# ============================================================================================ palette
SUN_DIR_RIM = hexc("#ff9a6a")      # warm rim light from the low sun on the left

PAL = {
    # Claude
    "O": hexc("#f47e50"), "o": hexc("#ffa676"), "S": hexc("#ba5232"), "K": hexc("#18141e"),
    # canopy
    "B": hexc("#3a5c96"), "b": hexc("#6088c4"), "N": hexc("#142e5c"), "C": hexc("#faeacc"), "c": hexc("#dcc0a0"),
    # helicopter
    "W": hexc("#eee4d2"), "V": hexc("#fff8ec"), "w": hexc("#c4b8ac"), "x": hexc("#8c7e84"),
    "G": hexc("#2a426c"), "g": hexc("#6ea0ce"), "h": hexc("#c4e4f6"), "D": hexc("#161222"), "d": hexc("#30283e"),
    "M": hexc("#4e5062"), "m": hexc("#848698"), "r": hexc("#ff4848"), "y": hexc("#ffd678"), "n": hexc("#5af08c"),
    "L": hexc("#121020"),
}


class Sprite:
    __slots__ = ("rgb", "mask", "w", "h")

    def __init__(self, rgb, mask):
        self.rgb, self.mask = rgb, mask
        self.h, self.w = mask.shape

    @classmethod
    def from_rows(cls, rows, pal=PAL):
        h, w = len(rows), max(len(r) for r in rows)
        rgb = np.zeros((h, w, 3), np.uint8)
        mask = np.zeros((h, w), bool)
        for y, row in enumerate(rows):
            for x, ch in enumerate(row):
                if ch != ".":
                    rgb[y, x] = pal[ch]
                    mask[y, x] = True
        return cls(rgb, mask)

    def flipped(self):
        return Sprite(self.rgb[:, ::-1].copy(), self.mask[:, ::-1].copy())

    def tinted(self, k, toward=(0, 0, 0)):
        rgb = self.rgb.astype(np.float32) * (1 - k) + np.array(toward, np.float32) * k
        return Sprite(rgb.astype(np.uint8), self.mask)


def blit(dst, spr, x, y, clip=None):
    """Copy the opaque pixels of spr to dst at (x, y), clipped to the image and to clip=(x0, y0, x1, y1)."""
    H, W = dst.shape[:2]
    if clip is None:
        cx0, cy0, cx1, cy1 = 0, 0, W, H
    else:
        cx0, cy0, cx1, cy1 = max(0, clip[0]), max(0, clip[1]), min(W, clip[2]), min(H, clip[3])
    x, y = int(x), int(y)
    sx0, sy0 = max(0, cx0 - x), max(0, cy0 - y)
    sx1, sy1 = min(spr.w, cx1 - x), min(spr.h, cy1 - y)
    if sx0 >= sx1 or sy0 >= sy1:
        return
    reg = dst[y + sy0:y + sy1, x + sx0:x + sx1]
    m = spr.mask[sy0:sy1, sx0:sx1]
    reg[m] = spr.rgb[sy0:sy1, sx0:sx1][m]


def fill(img, x0, y0, x1, y1, c):
    """Fill [x0, x1) x [y0, y1)."""
    H, W = img.shape[:2]
    x0, y0, x1, y1 = max(0, int(x0)), max(0, int(y0)), min(W, int(x1)), min(H, int(y1))
    if x0 < x1 and y0 < y1:
        img[y0:y1, x0:x1] = c


def put(img, x, y, c):
    x, y = int(x), int(y)
    if 0 <= y < img.shape[0] and 0 <= x < img.shape[1]:
        img[y, x] = c


def add_light(img, x0, y0, alpha, color):
    """Additive light: img[y0:, x0:] += color * alpha, clipped. alpha is a 2-D float array."""
    H, W = img.shape[:2]
    h, w = alpha.shape
    ax0, ay0 = max(0, -x0), max(0, -y0)
    ax1, ay1 = min(w, W - x0), min(h, H - y0)
    if ax0 >= ax1 or ay0 >= ay1:
        return
    reg = img[y0 + ay0:y0 + ay1, x0 + ax0:x0 + ax1]
    add = alpha[ay0:ay1, ax0:ax1, None] * np.array(color, np.float32)
    reg[...] = np.minimum(reg.astype(np.float32) + add, 255).astype(np.uint8)


@lru_cache(maxsize=64)
def halo(r, strength, steps=4):
    """Banded radial glow (pixel-art style: a few flat rings, not a smooth blur)."""
    yy, xx = np.mgrid[-r:r + 1, -r:r + 1].astype(np.float32)
    d = np.sqrt(xx * xx + yy * yy) / (r + 0.5)
    a = np.clip(1 - d, 0, 1) ** 1.6
    a = np.ceil(a * steps) / steps * strength
    a[d >= 1] = 0
    return a


def glow(img, cx, cy, r, color, strength=0.5, steps=4):
    add_light(img, int(cx) - r, int(cy) - r, halo(r, strength, steps), color)


def line_pts(x0, y0, x1, y1):
    x0, y0, x1, y1 = int(round(x0)), int(round(y0)), int(round(x1)), int(round(y1))
    dx, dy = abs(x1 - x0), -abs(y1 - y0)
    sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
    err = dx + dy
    pts = []
    while True:
        pts.append((x0, y0))
        if x0 == x1 and y0 == y1:
            return pts
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy


def curve_pts(x0, y0, x1, y1, sag):
    """A rope from (x0, y0) to (x1, y1) hanging down by `sag` pixels in the middle (quadratic Bezier)."""
    if abs(sag) < 0.5:
        return line_pts(x0, y0, x1, y1)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2 + 2 * sag
    n = int(abs(x1 - x0) + abs(y1 - y0) + 2 * abs(sag)) + 2
    pts, last = [], None
    for i in range(n + 1):
        u = i / n
        x = (1 - u) ** 2 * x0 + 2 * (1 - u) * u * cx + u * u * x1
        y = (1 - u) ** 2 * y0 + 2 * (1 - u) * u * cy + u * u * y1
        p = (int(round(x)), int(round(y)))
        if p != last:
            pts.append(p)
            last = p
    return pts


def draw_pts(img, pts, c):
    for x, y in pts:
        put(img, x, y, c)


# ============================================================================================ char canvas
def point_in_poly(x, y, pts):
    inside = False
    j = len(pts) - 1
    for i in range(len(pts)):
        xi, yi = pts[i]
        xj, yj = pts[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


class Chars:
    """A grid of palette characters for building sprites from shapes ('.' is transparent)."""

    def __init__(self, w, h):
        self.w, self.h = w, h
        self.g = [["."] * w for _ in range(h)]

    def get(self, x, y):
        return self.g[y][x] if 0 <= x < self.w and 0 <= y < self.h else "."

    def set(self, x, y, c, only=None):
        if 0 <= x < self.w and 0 <= y < self.h and (only is None or self.g[y][x] in only):
            self.g[y][x] = c

    def rect(self, x0, y0, x1, y1, c, only=None):          # inclusive corners
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                self.set(x, y, c, only)

    def ellipse(self, cx, cy, rx, ry, c, only=None):
        for y in range(int(cy - ry) - 1, int(cy + ry) + 2):
            for x in range(int(cx - rx) - 1, int(cx + rx) + 2):
                if ((x + 0.5 - cx) / rx) ** 2 + ((y + 0.5 - cy) / ry) ** 2 <= 1.0:
                    self.set(x, y, c, only)

    def poly(self, pts, c, only=None):
        ys = [p[1] for p in pts]
        xs = [p[0] for p in pts]
        for y in range(int(min(ys)) - 1, int(max(ys)) + 2):
            for x in range(int(min(xs)) - 1, int(max(xs)) + 2):
                if point_in_poly(x + 0.5, y + 0.5, pts):
                    self.set(x, y, c, only)

    def outline(self, c, skip="."):
        add = []
        for y in range(self.h):
            for x in range(self.w):
                if self.g[y][x] != ".":
                    continue
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    n = self.get(x + dx, y + dy)
                    if n != "." and n != c and n not in skip:
                        add.append((x, y))
                        break
        for x, y in add:
            self.g[y][x] = c

    def rows(self):
        return ["".join(r) for r in self.g]


# ============================================================================================ fonts
FONT5 = {  # 5x7 for the big neon sign
    "C": [".###.", "#...#", "#....", "#....", "#....", "#...#", ".###."],
    "L": ["#....", "#....", "#....", "#....", "#....", "#....", "#####"],
    "A": [".###.", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"],
    "U": ["#...#", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."],
    "D": ["####.", "#...#", "#...#", "#...#", "#...#", "#...#", "####."],
    "E": ["#####", "#....", "#....", "####.", "#....", "#....", "#####"],
}
FONT3 = {  # 3x5 for shop signs
    "A": [".#.", "#.#", "###", "#.#", "#.#"], "B": ["##.", "#.#", "##.", "#.#", "##."],
    "C": [".##", "#..", "#..", "#..", ".##"], "D": ["##.", "#.#", "#.#", "#.#", "##."],
    "E": ["###", "#..", "##.", "#..", "###"], "F": ["###", "#..", "##.", "#..", "#.."],
    "H": ["#.#", "#.#", "###", "#.#", "#.#"], "I": ["###", ".#.", ".#.", ".#.", "###"],
    "L": ["#..", "#..", "#..", "#..", "###"], "M": ["#.#", "###", "###", "#.#", "#.#"],
    "O": ["###", "#.#", "#.#", "#.#", "###"], "P": ["##.", "#.#", "##.", "#..", "#.."],
    "R": ["##.", "#.#", "##.", "#.#", "#.#"], "S": [".##", "#..", ".#.", "..#", "##."],
    "T": ["###", ".#.", ".#.", ".#.", ".#."], "X": ["#.#", "#.#", ".#.", "#.#", "#.#"],
    "Z": ["###", "..#", ".#.", "#..", "###"], "2": ["##.", "..#", ".#.", "#..", "###"],
    "4": ["#.#", "#.#", "###", "..#", "..#"], "7": ["###", "..#", ".#.", ".#.", ".#."],
    "/": ["..#", "..#", ".#.", "#..", "#.."], " ": ["...", "...", "...", "...", "..."],
}


def text_mask(text, font, gap=1):
    glyphs = [font[ch] for ch in text]
    gh, gw = len(glyphs[0]), len(glyphs[0][0])
    m = np.zeros((gh, len(glyphs) * (gw + gap) - gap), bool)
    for i, g in enumerate(glyphs):
        for y, row in enumerate(g):
            for x, ch in enumerate(row):
                if ch == "#":
                    m[y, i * (gw + gap) + x] = True
    return m


def dilate(mask, r):
    out = mask.copy()
    h, w = mask.shape
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            if dx * dx + dy * dy > r * r + r:
                continue
            ys, yd = (slice(0, h - dy), slice(dy, h)) if dy >= 0 else (slice(-dy, h), slice(0, h + dy))
            xs, xd = (slice(0, w - dx), slice(dx, w)) if dx >= 0 else (slice(-dx, w), slice(0, w + dx))
            out[yd, xd] |= mask[ys, xs]
    return out


def neon_glow_alpha(mask, pad=6):
    """Banded glow around a neon mask: returns (alpha, pad) with alpha the size of mask + 2*pad."""
    h, w = mask.shape
    m = np.zeros((h + 2 * pad, w + 2 * pad), bool)
    m[pad:pad + h, pad:pad + w] = mask
    a = np.zeros(m.shape, np.float32)
    for r, s in ((pad, 0.07), (pad // 2 + 1, 0.11), (1, 0.22)):
        a = np.maximum(a, dilate(m, r) * s)
    a[m] = 0
    return a


# ============================================================================================ Claude
BODY = [
    ".ooOOOOOOOOOOOS.",
    "oOOOOOOOOOOOOOOS",
    "oOOOOOOOOOOOOOOS",
    "oOOOOOOOOOOOOOOS",
    "oOOOOOOOOOOOOOOS",
    "oOOOOOOOOOOOOOOS",
    "oOOOOOOOOOOOOOOS",
    "oOOOOOOOOOOOOOOS",
    "oOOOOOOOOOOOOOOS",
    "oOOOOOOOOOOOOOSS",
    ".SSSSSSSSSSSSSS.",
]
EYES = {  # (rows, left eye column, right eye column)
    "normal": ([".K", "KK", "KK"], 4, 10),
    "blink": (["..", "..", "KK"], 4, 10),
    "wide": (["KK", "KK", "KK"], 4, 10),
    "down": (["..", ".K", "KK"], 4, 10),
    "happy": ([".K.", "K.K", "..."], 3, 10),
}
# left arm pixels (dx, dy) relative to the body's top-left corner; the right arm is mirrored
ARMS = {
    "hold": [(-4, -2, "o"), (-3, -2, "O"), (-4, -1, "o"), (-3, -1, "O"), (-2, -1, "O"), (-3, 0, "o"), (-2, 0, "O"),
             (-1, 0, "O"), (-2, 1, "o"), (-1, 1, "O")],
    "spread": [(-6, 0, "o"), (-5, 0, "O"), (-6, 1, "o"), (-5, 1, "O"), (-4, 1, "O"), (-5, 2, "o"), (-4, 2, "O"),
               (-3, 2, "O"), (-2, 2, "O"), (-1, 2, "O"), (-3, 3, "S"), (-2, 3, "S"), (-1, 3, "O")],
    "down": [(-1, 4, "o"), (-2, 5, "o"), (-1, 5, "O"), (-2, 6, "o"), (-1, 6, "O"), (-2, 7, "S"), (-1, 7, "S")],
    "cheer": [(-2, -4, "o"), (-1, -4, "O"), (-2, -3, "o"), (-1, -3, "O"), (-2, -2, "o"), (-1, -2, "O"),
              (-1, -1, "o"), (-1, 0, "O"), (-1, 1, "O")],
    "carry": [(-1, 3, "o"), (-1, 2, "o"), (-2, 1, "o"), (-1, 1, "O"), (-2, 0, "o"), (-1, 0, "O"), (-1, -1, "o"),
              (0, -1, "O"), (-1, -2, "o"), (0, -2, "O"), (0, -3, "o")],
}
LEGS = {  # lengths of the four legs, or "spread"
    "stand": (3, 3, 3, 3), "a": (3, 2, 3, 2), "b": (2, 3, 2, 3), "squash": (2, 2, 2, 2), "tuck": (1, 1, 1, 1),
}
LEG_COLS = (1, 4, 10, 13)
GROUNDED = {"stand", "squash"}       # short grounded legs lower the body; short other legs are lifted
BUNDLE = [
    "...bbBBBBB...",
    ".bBBCCCCCBBB.",
    "bBCCCCCCCCBBN",
    "BBBBBBBBBBBNN",
    "NBBBNBBBBBNNN",
    ".NNNNNNNNNNN.",
]
# The anchor is the middle of the bottom row of the feet.
C_W, C_H, C_BX, C_BY = 30, 34, 7, 16          # canvas and where the body's top-left goes
C_AX, C_AY = C_BX + 8, C_BY + 13               # anchor (feet bottom, between the legs)


@lru_cache(maxsize=256)
def claude(arms="down", legs="stand", eyes="normal", squash=0, mouth=False, bundle=False):
    """Claude composed from parts. Returns (Sprite, anchor_x, anchor_y)."""
    cv = Chars(C_W, C_H)
    body = list(BODY)
    if squash > 0:
        body = body[:5] + body[5 + squash:]
    elif squash < 0:
        body = body[:5] + [body[5]] * (-squash) + body[5:]
    drop = 3 - max(LEGS[legs]) if legs in GROUNDED else 0
    top = C_BY + (len(BODY) - len(body)) + drop    # keep the feet on the ground
    for y, row in enumerate(body):
        for x, ch in enumerate(row):
            if ch != ".":
                cv.set(C_BX + x, top + y, ch)
    erows, el, er = EYES[eyes]
    for y, row in enumerate(erows):
        for x, ch in enumerate(row):
            if ch == "K":
                cv.set(C_BX + el + x, top + 3 + y, "K")
                cv.set(C_BX + er + (len(row) - 1 - x if eyes == "happy" else x), top + 3 + y, "K")
    if mouth:
        cv.rect(C_BX + 7, top + 7, C_BX + 8, top + 8, "K")
    swap = {"o": "S", "O": "O", "S": "S"}
    for dx, dy, ch in ARMS[arms]:
        cv.set(C_BX + dx, top + dy, ch)
        cv.set(C_BX + 15 - dx, top + dy, swap[ch] if ch == "o" else ch)
    foot = C_AY
    if legs == "spread":
        for (x, y) in ((0, 1), (1, 1), (-1, 2), (0, 2), (-2, 3), (-1, 3)):
            cv.set(C_BX + x, foot - 3 + y, "O" if x % 2 == 0 else "S")
            cv.set(C_BX + 15 - x, foot - 3 + y, "S" if x % 2 == 0 else "O")
        for c in (4, 10):
            cv.rect(C_BX + c, foot - 2, C_BX + c, foot - 1, "O")
            cv.rect(C_BX + c + 1, foot - 2, C_BX + c + 1, foot - 1, "S")
    else:
        for c, n in zip(LEG_COLS, LEGS[legs]):
            y0 = foot - n + 1 if legs in GROUNDED else foot - 2
            for k in range(n):
                cv.set(C_BX + c, y0 + k, "O")
                cv.set(C_BX + c + 1, y0 + k, "S")
    if bundle:
        for y, row in enumerate(BUNDLE):
            for x, ch in enumerate(row):
                if ch != ".":
                    cv.set(C_BX + 1 + x, top - 3 - len(BUNDLE) + y + 1, ch)
    return Sprite.from_rows(cv.rows()), C_AX, C_AY


def claude_hands(arms):
    """Hand pixels (left outer, left inner, right inner, right outer) relative to the anchor, for the rigging."""
    if arms == "hold":
        lo, li = (-4, -2), (-3, -2)
    elif arms == "spread":
        lo, li = (-6, 0), (-5, 0)
    else:
        lo, li = (0, 0), (1, 0)
    def rel(p, mirror=False):
        x = 15 - p[0] if mirror else p[0]
        return C_BX + x - C_AX, C_BY + p[1] - C_AY
    return rel(lo), rel(li), rel(li, True), rel(lo, True)


# ============================================================================================ canopy
@lru_cache(maxsize=1024)
def canopy(w, B=16.0, C=12.0, Y1=11.0, tilt=0, flat=False):
    """A round canopy seen from the side and a little below: blue dome, cream band, navy underside.

    Returns (Sprite, attach) where attach = ((x, y) of left tip, left inner, right inner, right tip) in sprite
    coordinates, i.e. where the lines leave the canopy's lower edge."""
    w = max(5, int(w) | 1)
    a = w / 2
    tops, bots, us = [], [], []
    for i in range(w):
        u = (i + 0.5 - a) / a
        s = math.sqrt(max(0.0, 1 - u * u))
        off = tilt * u
        tops.append(B * (1 - s) + off)
        bots.append(Y1 + C * (1 - s) + off)
        us.append(u)
    lo = math.floor(min(tops))
    hgt = int(math.ceil(max(bots))) - lo + 1
    cv = Chars(w, hgt)
    for i in range(w):
        t, b, u = tops[i] - lo, bots[i] - lo, us[i]
        for y in range(hgt):
            yc = y + 0.5
            if not (t <= yc < b):
                continue
            ch = "B"
            if yc >= b - 2.2 or (abs(u) > 0.9 and yc >= b - 4):
                ch = "N"
            elif abs(u) < 0.6 and t + 2 <= yc < t + 6.5 and not flat:
                ch = "C"
            elif yc < t + 1.2 and u < -0.1:
                ch = "b"
            elif u < -0.86:
                ch = "b"
            cv.set(i, y, ch)
    def at(u):
        i = min(w - 1, max(0, int(round((u + 1) * a - 0.5))))
        col = [y for y in range(hgt) if cv.get(i, y) != "."]
        return i, (max(col) if col else hgt - 1)
    attach = (at(-0.99), at(-0.45), at(0.45), at(0.99))
    return Sprite.from_rows(cv.rows()), attach


PILE = [
    "...........bbBBBBBBBB...............",
    "........bBBBBCCCCCCCCBBBBB..........",
    ".....bBBBBCCCCCCCCCCCCCCBBBBBBBb....",
    "...bBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB..",
    ".NNBBBBBBBNNNBBBBBBBBBNNNBBBBBBBBNN.",
    "NNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNN",
]
PILOT = [".bBB.", "bBBBN", ".NNN."]


# ============================================================================================ helicopter
HUB = (55, 1)               # main rotor hub in sprite coordinates
TAIL_HUB = (6, 4)
DOOR = (46, 9, 63, 23)      # door opening [x0, x1) x [y0, y1)


def heli_rows():
    cv = Chars(86, 30)
    # tail boom and fin
    cv.poly([(8, 9.5), (42, 7.5), (42, 17), (8, 13.5)], "W")
    cv.poly([(1, 0), (8, 0), (15, 10), (15, 15), (6, 15), (3, 8)], "W")
    cv.rect(12, 14, 20, 15, "W")                                # horizontal stabiliser
    # cabin, rounded nose and back, engine cowling
    cv.rect(38, 7, 66, 24, "W")
    cv.ellipse(66, 16, 15, 9.2, "W")
    cv.ellipse(42, 16, 7, 9.2, "W")
    cv.rect(44, 3, 66, 7, "W")
    cv.ellipse(45, 5.5, 3, 2.6, "W")
    cv.ellipse(66, 5.5, 4, 2.6, "W")
    # shading: belly, lower boom, fin edge; highlight on top
    for y in range(21, 26):
        for x in range(cv.w):
            if cv.get(x, y) == "W":
                cv.set(x, y, "w" if y < 24 else "x")
    for x in range(8, 42):
        col = [y for y in range(cv.h) if cv.get(x, y) == "W"]
        if col:
            cv.set(x, max(col), "w")
            cv.set(x, min(col), "V")
    for x in range(44, 67):
        cv.set(x, 3, "V", only="W")
    for x in range(38, 80):
        col = [y for y in range(4, 12) if cv.get(x, y) == "W"]
        if col and x > 66:
            cv.set(x, min(col), "V")
    # orange stripe along the body and boom, orange fin tip
    for x in range(8, 82):
        for y, ch in ((18, "O"), (19, "S")):
            cv.set(x, y, ch, only="Ww")
    for x in range(8, 42):
        col = [y for y in range(cv.h) if cv.get(x, y) in "WV"]
        if len(col) >= 3:
            mid = (min(col) + max(col)) // 2 + 1
            cv.set(x, mid, "O", only="W")
    for y in range(0, 4):
        for x in range(0, 10):
            cv.set(x, y, "O", only="WV")
    # windscreen with pilot, highlight streak
    cv.poly([(67, 8), (74, 8.5), (80.5, 15), (80.5, 16.5), (67, 16.5)], "G", only="WV")
    for k in range(7):
        cv.set(70 + k, 9 + k, "g", only="G")
        cv.set(71 + k, 9 + k, "h", only="G")
    cv.rect(69, 11, 71, 13, "D", only="Gg")
    cv.rect(69, 10, 70, 10, "M", only="Gg")
    # open door (dark cabin), slid-back door panel with its window
    x0, y0, x1, y1 = DOOR
    cv.rect(x0, y0, x1 - 1, y1 - 1, "D")
    cv.rect(x0, y0, x1 - 1, y0, "d")
    cv.rect(x0 - 1, y0 - 1, x1, y0 - 1, "x", only="WVw")
    cv.rect(x1, y0, x1, y1 - 1, "x", only="WVwOS")
    cv.rect(34, 9, 45, 22, "w")
    cv.rect(34, 9, 45, 9, "x")
    cv.rect(36, 11, 43, 15, "G")
    cv.rect(37, 12, 38, 13, "g")
    cv.rect(34, 18, 45, 18, "O")
    cv.rect(34, 19, 45, 19, "S")
    # exhaust and intake
    cv.rect(44, 5, 45, 6, "M")
    for x in range(58, 64, 2):
        cv.set(x, 5, "x")
    cv.outline("L")
    # mast, skids and struts after the outline so they stay thin
    cv.rect(54, 0, 56, 2, "M")
    cv.rect(53, 0, 57, 0, "L")
    for x0_, x1_ in ((48, 46), (70, 72)):
        for y in range(26, 29):
            cv.set(x0_ + (x1_ - x0_) * (y - 26) // 2, y, "M")
    cv.rect(38, 29, 79, 29, "M")
    cv.set(80, 28, "M")
    cv.set(81, 27, "M")
    cv.rect(39, 29, 78, 29, "m", only="M")
    cv.set(38, 29, "L")
    return cv.rows()


def shear(rows, tilt, pivot):
    """Tilt a side-view sprite by shearing columns (nose down for tilt > 0). Returns (rows, top_offset, offsets)."""
    w = len(rows[0])
    offs = [int(round(tilt * (x - pivot) / 40.0)) for x in range(w)]
    lo, hi = min(offs), max(offs)
    out = [["."] * w for _ in range(len(rows) + hi - lo)]
    for x in range(w):
        for y, row in enumerate(rows):
            if row[x] != ".":
                out[y + offs[x] - lo][x] = row[x]
    return ["".join(r) for r in out], lo, offs


@lru_cache(maxsize=16)
def heli(tilt):
    rows, lo, offs = shear(heli_rows(), tilt, HUB[0])
    return Sprite.from_rows(rows), lo, tuple(offs)


# ============================================================================================ vehicles
CAR_W, CAR_H = 40, 14
CAR_COLORS = {"taxi": "#f4c034", "sport": "#d8303c", "teal": "#2a9e98", "white": "#e4e0ea", "police": "#18161f",
              "van": "#c6c0d0", "purple": "#8a4cc6"}


def car_rows(kind):
    """Side view, facing right. Body 'A' (with highlight 'a' and shade 'Z'), glass 'G', tyres 'L'."""
    cv = Chars(CAR_W, CAR_H)
    if kind == "van":
        cv.rect(2, 1, 36, 10, "A")
        cv.poly([(31, 1), (35, 1), (39, 6), (39, 10), (31, 10)], "A")
        cv.rect(6, 3, 15, 6, "G")
        cv.rect(18, 3, 27, 6, "G")
        cv.poly([(31, 2.5), (34.5, 2.5), (37.5, 6.5), (31, 6.5)], "G")
        cv.rect(2, 1, 35, 1, "a")
        cv.rect(29, 2, 29, 10, "Z")
    else:
        low = kind == "sport"
        roof = 3 if low else 1
        cv.poly([(12, roof), (25, roof), (31.5, 6), (6.5, 6)], "A")              # greenhouse
        cv.poly([(13, roof + 1), (24.5, roof + 1), (29.5, 6), (8.5, 6)], "G")
        cv.rect(18, roof + 1, 19, 5, "A")                                       # B-pillar
        cv.rect(2, 6, 38, 10, "A")
        cv.rect(1, 7, 39, 10, "A")
        cv.rect(4, 6, 36, 6, "a")                                               # beltline highlight
        if low:
            cv.rect(1, 5, 4, 5, "A")                                             # spoiler
            cv.rect(1, 4, 2, 4, "A")
    for x in range(cv.w):                                                       # lower body shade
        cv.set(x, 10, "Z", only="A")
    cv.rect(19, 7, 19, 9, "Z", only="A")                                        # door seams
    if kind != "van":
        cv.rect(28, 7, 28, 9, "Z", only="A")
    if kind == "taxi":
        cv.rect(14, 0, 22, 0, "Y")
        for x in range(3, 37, 2):
            cv.set(x, 8, "L", only="A")
    if kind == "police":
        for y in range(5, 10):
            for x in range(10, 30):
                cv.set(x, y, "W", only="Aa")
        cv.rect(14, 0, 23, 0, "M")
    for x0 in (5, 28):                                                          # wheel arches and tyres
        cv.rect(x0 - 1, 9, x0 + 6, 10, ".", only="AZaW")
        cv.ellipse(x0 + 2.5, 11, 3.6, 3.0, "L")
        cv.rect(x0 + 2, 10, x0 + 3, 11, "m")
    cv.rect(38, 7, 39, 7, "H")                                                  # headlight
    cv.rect(1, 7, 1, 8, "T")                                                    # tail light
    cv.rect(37, 10, 39, 10, "m", only="AZ")                                     # bumpers
    cv.rect(1, 10, 3, 10, "m", only="AZ")
    cv.outline("L", skip="GHTmY")
    return cv.rows()


@lru_cache(maxsize=32)
def car(kind, facing_right=True):
    body = hexc(CAR_COLORS[kind])
    pal = dict(PAL)
    pal.update({"A": body, "a": mix(body, (255, 255, 255), 0.35), "Z": shade(body, 0.62), "W": hexc("#ecebf2"),
                "H": hexc("#fff4c8"), "T": hexc("#ff3c3c"), "Y": hexc("#fff07a"), "G": hexc("#323c64"),
                "L": hexc("#0e0c18"), "m": hexc("#9a9cb0"), "M": hexc("#4a4c5c")})
    spr = Sprite.from_rows(car_rows(kind), pal)
    return spr if facing_right else spr.flipped()


# ============================================================================================ the city
class Layer:
    """An opaque-pixel layer with a mask, composited onto the frame each time."""

    def __init__(self, h, w):
        self.rgb = np.zeros((h, w, 3), np.uint8)
        self.mask = np.zeros((h, w), bool)

    def fill(self, x0, y0, x1, y1, c):
        fill(self.rgb, x0, y0, x1, y1, c)
        H, W = self.mask.shape
        x0, y0, x1, y1 = max(0, int(x0)), max(0, int(y0)), min(W, int(x1)), min(H, int(y1))
        if x0 < x1 and y0 < y1:
            self.mask[y0:y1, x0:x1] = True

    def put(self, x, y, c):
        if 0 <= y < self.mask.shape[0] and 0 <= x < self.mask.shape[1]:
            self.rgb[y, x] = c
            self.mask[y, x] = True

    def bounds(self):
        rows = np.where(self.mask.any(axis=1))[0]
        return (int(rows[0]), int(rows[-1]) + 1) if len(rows) else (0, 0)

    def over(self, out):
        y0, y1 = getattr(self, "_b", None) or self.bounds()
        self._b = (y0, y1)
        np.copyto(out[y0:y1], self.rgb[y0:y1], where=self.mask[y0:y1, :, None])


SKY_STOPS = [(0, "#0c0e2a"), (55, "#15143e"), (115, "#281c58"), (175, "#46266e"), (225, "#70307c"),
             (268, "#9e3a7c"), (308, "#cc4e72"), (342, "#ec6c62"), (372, "#fa9460"), (394, "#ffbc72"),
             (410, "#ffd488"), (559, "#ffdc96")]

HORIZON = 398
ROOF_Y = 330                 # the landing tower's roof line (Claude's feet stand at ROOF_Y - 1)
TOWER = (466, 590)           # landing tower x range
STAIR = (548, 584, 309)      # stairwell box x0, x1, top
DOOR_R = (557, 313, 571, 330)  # roof door [x0, x1) x [y0, y1)
LOBBY_DOOR = (548, 470, 561, 484)
SIDEWALK_Y = 484
LANE_UP, LANE_LO = 500, 511  # bottom rows of the two lanes (upper lane: left to right, lower: right to left)
SEAWALL = 520
# The player shows a still picture (frame 0) behind the menu bar and the Dock, so those bands of the art grid must
# never change: rows [0, STILL_TOP) and [STILL_BOTTOM, ART_H). test_scene.py checks every frame. With the 16" panel,
# the menu bar (33 pt) and a Dock of tile size 67 (93 pt) fall inside them; a bigger Dock makes the player animate
# that strip instead.
STILL_TOP, STILL_BOTTOM = 18, 512


def sky_color(y):
    for (y0, c0), (y1, c1) in zip(SKY_STOPS, SKY_STOPS[1:]):
        if y <= y1:
            return mix(hexc(c0), hexc(c1), (y - y0) / (y1 - y0))
    return hexc(SKY_STOPS[-1][1])


class City:
    def __init__(self, seed=7):
        self.rng = random.Random(seed)
        self.W, self.H = ART_W, ART_H
        self.blinkers = []       # (x, y, color, cycles, phase, duty, glow_r)
        self.toggles = []        # (x0, y0, x1, y1, on_color, off_color, a, b) lit while a <= cyc(t,1) < b
        self.neons = []          # dict(mask, x, y, color, glow, flicker)
        self.build_sky()
        self.build_clouds()
        self.build_far()
        n_far = len(self.blinkers)
        self.build_bridge()
        n_bridge = len(self.blinkers)
        self.build_mid()
        # aircraft warning lights belong to their layer, so nearer buildings and Claude cover far ones
        self.blinker_layers = {"far": self.blinkers[:n_far], "bridge": self.blinkers[n_far:n_bridge],
                               "mid": self.blinkers[n_bridge:]}
        self.build_near()
        self.build_beams()
        self.build_water()

    # ------------------------------------------------------------------------------------------ sky
    def build_sky(self):
        W, H = self.W, self.H
        band = 7.0
        n = int(H / band) + 2
        cols = np.array([sky_color((i + 0.5) * band) for i in range(n)], np.float32)
        ys = np.arange(H, dtype=np.float32)[:, None] / band
        f = np.floor(ys)
        frac = ys - f
        thr = bayer(H, W)
        step = np.clip((frac - 0.55) / 0.45, 0, 1) > thr
        idx = np.clip(f.astype(int) + step.astype(int), 0, n - 1)
        sky = cols[idx].astype(np.uint8)
        self.sky_rgb = sky
        self.draw_sun(sky)
        self.draw_moon(sky)
        # stars: denser and brighter high up
        rng = self.rng
        stars = []
        for _ in range(170):
            y = int(205 * rng.random() ** 1.7)
            x = rng.randrange(W)
            b = rng.choice([0.35, 0.5, 0.65, 0.8, 1.0])
            col = rng.choice([(255, 255, 255), (210, 224, 255), (255, 240, 210)])
            stars.append((x, y, b * (1 - y / 260), col, rng.randint(3, 19), rng.random()))
        self.stars = stars
        self.star_xy = (np.array([s[1] for s in stars]), np.array([s[0] for s in stars]))
        self.star_base = sky[self.star_xy].astype(np.float32)
        self.star_col = np.array([s[3] for s in stars], np.float32)
        self.star_b = np.array([s[2] for s in stars], np.float32)
        self.star_n = np.array([s[4] for s in stars], np.float32)
        self.star_ph = np.array([s[5] for s in stars], np.float32)
        self.bright_stars = [(rng.randrange(40, W - 40), rng.randrange(24, 120), rng.randint(2, 7), rng.random())
                             for _ in range(7)]

    def draw_sun(self, sky):
        cx, cy, r = 212, 374, 46
        self.sun = (cx, cy, r)
        glow(sky, cx, cy, 120, (255, 150, 90), 0.20, steps=5)
        glow(sky, cx, cy, 72, (255, 190, 120), 0.22, steps=3)
        top = [(255, 246, 188), (255, 222, 140), (255, 190, 108), (255, 156, 96), (250, 122, 100)]
        for y in range(cy - r, cy + r + 1):
            dy = y - cy
            hw = math.sqrt(max(0, r * r - dy * dy))
            if dy > 4:                          # horizontal slits in the lower half
                k = dy - 4
                gap = [2, 1, 2, 2, 3, 3]
                period = 7
                if (k % period) < min(gap[min(len(gap) - 1, k // period)], 1 + k // 9):
                    continue
            c = top[min(len(top) - 1, int((dy + r) / (2 * r) * len(top)))]
            for x in range(int(cx - hw), int(cx + hw) + 1):
                put(sky, x, y, c)

    def draw_moon(self, sky):
        cx, cy, r = 652, 62, 8
        glow(sky, cx, cy, 26, (200, 190, 255), 0.10, steps=3)
        for y in range(cy - r, cy + r + 1):
            for x in range(cx - r, cx + r + 1):
                if (x - cx) ** 2 + (y - cy) ** 2 <= r * r + 2 and (x - cx - 4) ** 2 + (y - cy + 3) ** 2 > (r - 1) ** 2:
                    put(sky, x, y, (252, 240, 214) if x < cx - 2 else (236, 222, 200))

    # ------------------------------------------------------------------------------------------ clouds
    def build_clouds(self):
        """Wrap-around cloud strips. Each strip is P wide and slides P pixels per loop: seamless by construction."""
        self.cloud_layers = []
        specs = [  # (period, y0, y1, clouds, colors: top, body, under, lit)
            (ART_W, 60, 136, [(40, 104, 150, 12), (300, 86, 104, 10), (520, 116, 170, 13), (770, 84, 96, 10)],
             [(30, 24, 70), (46, 32, 88), (74, 44, 108), (124, 62, 126), (186, 92, 136), (232, 128, 146)]),
            (int(ART_W * 1.5), 126, 256, [(60, 200, 110, 22), (330, 168, 80, 16), (560, 218, 136, 24),
                                         (900, 182, 96, 18), (1150, 226, 84, 15)],
             [(44, 30, 86), (70, 40, 104), (112, 54, 122), (176, 78, 132), (236, 118, 138), (255, 170, 142)]),
        ]
        for period, y0, y1, clouds, ramp in specs:
            h = y1 - y0
            mask = np.zeros((h, period), bool)
            bottom = np.zeros((h, period), np.int16)
            rng = self.rng
            top = np.full((h, period), 0, np.int16)
            for cx, cy, w, ch in clouds:
                # cumulus: a row of round puffs, tallest in the middle, cut by a flat base
                base = cy + ch * 0.35
                n = max(4, w // 11)
                puffs = []
                for i in range(n):
                    u = (i + 0.5) / n
                    px = cx - w / 2 + u * w + rng.uniform(-2, 2)
                    r_ = ch * (0.45 + 0.55 * math.sin(math.pi * u) ** 0.8) * rng.uniform(0.75, 1.05)
                    puffs.append((px, base - r_ * 0.55, max(3.0, r_ * 1.15), max(2.0, r_)))
                ctop = min(p[1] - p[3] for p in puffs)
                for px, py, rx, ry in puffs:
                    for y in range(int(py - ry) - 1, int(base) + 1):
                        for x in range(int(px - rx) - 1, int(px + rx) + 2):
                            inside = ((x + 0.5 - px) / rx) ** 2 + ((y + 0.5 - py) / ry) ** 2 <= 1
                            if inside or (py <= y <= base and abs(x + 0.5 - px) <= rx * 0.9):
                                yy = y - y0
                                if 0 <= yy < h and y <= base:
                                    mask[yy, x % period] = True
                                    bottom[yy, x % period] = int(base) - y0
                                    top[yy, x % period] = int(ctop) - y0
            rgb = np.zeros((h, period, 3), np.uint8)
            yy = np.arange(h)[:, None].repeat(period, 1)
            rel = np.clip((yy - top) / np.maximum(1, bottom - top), 0, 1) ** 1.3   # 0 at the top, 1 at the base
            th = bayer(h, period)
            idx = np.clip(np.floor(rel * (len(ramp) - 1) + th * 0.9), 0, len(ramp) - 1).astype(int)
            pal = np.array(ramp, np.uint8)
            rgb[:] = pal[idx]
            above = np.vstack([np.zeros((1, period), bool), mask[:-1]])
            left = np.hstack([mask[:, -1:], mask[:, :-1]])
            rgb[mask & ~above & (rel < 0.5)] = pal[min(1, len(ramp) - 1)]        # soft dark rim on top
            rgb[mask & ~left & (rel > 0.55)] = pal[-1]                          # sun side glows
            rgb[(bottom - yy <= 0) & mask] = pal[-1]
            self.cloud_layers.append((period, y0, rgb, mask))

    # ------------------------------------------------------------------------------------------ far skyline
    def build_far(self):
        L = Layer(self.H, self.W)
        rng = self.rng
        x = -6
        while x < self.W:
            w = rng.randint(9, 30)
            if 96 < x < 330:                        # leave the bay under the bridge open
                top = rng.randint(386, 404)
            else:
                top = rng.randint(296, 384)
            c = mix((92, 46, 102), (110, 56, 112), rng.random())
            L.fill(x, top, x + w, 450, c)
            L.fill(x, top, x + 1, 450, mix(c, SUN_DIR_RIM, 0.25))
            if rng.random() < 0.35 and top < 370:
                ax = x + rng.randint(2, max(2, w - 3))
                ah = rng.randint(4, 14)
                L.fill(ax, top - ah, ax + 1, top, c)
                if rng.random() < 0.5:
                    self.blinkers.append((ax, top - ah, (255, 70, 70), rng.choice([20, 24, 30, 40]), rng.random(), 0.4, 3))
            for wy in range(top + 3, 450, 3):
                for wx in range(x + 2, x + w - 1, 3):
                    if rng.random() < 0.12:
                        L.put(wx, wy, mix(c, (255, 190, 130), rng.choice([0.35, 0.5, 0.7])))
            x += w + rng.randint(-3, 2)
        self.far = L

    # ------------------------------------------------------------------------------------------ bridge
    def build_bridge(self):
        L = Layer(self.H, self.W)
        dark, mid, lamp = (44, 22, 58), (66, 34, 76), (255, 214, 140)
        deck, x0, x1 = 397, 70, 352
        towers = (148, 274)
        L.fill(x0, deck, x1, deck + 3, dark)
        L.fill(x0, deck + 3, x1, deck + 4, mid)
        for tx in towers:
            L.fill(tx - 2, 334, tx, 412, dark)
            L.fill(tx + 3, 334, tx + 5, 412, dark)
            for by in (340, 360, 384):
                L.fill(tx - 2, by, tx + 5, by + 2, dark)
            L.fill(tx - 2, 334, tx - 1, 412, mix(dark, SUN_DIR_RIM, 0.3))
            self.blinkers.append((tx + 1, 332, (255, 60, 60), 30, 0.1 if tx < 200 else 0.6, 0.35, 4))
        # main cable between towers plus side spans, suspenders down to the deck
        def cable(xa, ya, xb, yb, sag):
            pts = []
            for x in range(xa, xb + 1):
                u = (x - xa) / (xb - xa)
                y = ya + (yb - ya) * u + 4 * sag * u * (1 - u)
                pts.append((x, int(round(y))))
            return pts
        spans = [(x0, deck - 2, towers[0] + 1, 335, 6), (towers[0] + 1, 335, towers[1] + 1, 335, 52),
                 (towers[1] + 1, 335, x1, deck - 2, 6)]
        for xa, ya, xb, yb, sag in spans:
            pts = cable(xa, ya, xb, yb, sag)
            prev = None
            for x, y in pts:
                if prev:
                    for yy in range(min(prev, y), max(prev, y) + 1):
                        L.put(x, yy, mid)
                prev = y
                if x % 4 == 0 and y < deck:
                    for yy in range(y + 1, deck):
                        if not L.mask[yy, x]:
                            L.put(x, yy, (76, 40, 84))
        for x in range(x0 + 2, x1, 6):
            L.put(x, deck - 1, lamp)
        self.bridge = L
        self.bridge_deck = (x0, x1, deck)

    # ------------------------------------------------------------------------------------------ mid skyline
    def build_mid(self):
        L = Layer(self.H, self.W)
        self.mid = L
        rng = self.rng
        B = [  # x, w, top, style
            (-4, 40, 232, "deco"), (32, 56, 170, "spire"), (84, 38, 262, "grid"), (118, 28, 324, "strip"),
            (146, 34, 430, "low"), (186, 42, 438, "low"), (230, 40, 426, "low"), (268, 30, 434, "low"),
            (296, 46, 302, "glass"), (340, 56, 236, "stepped"), (394, 36, 318, "grid"), (426, 44, 276, "strip"),
            (586, 38, 292, "grid"), (622, 62, 196, "glass"), (680, 50, 138, "spire"), (728, 44, 250, "deco"),
            (770, 50, 302, "grid"), (814, 54, 228, "strip"),
        ]
        for x, w, top, style in sorted(B, key=lambda b: b[2]):
            self.building(L, rng, x, w, top, 472, style)
        self.landing_tower(L, rng)

    def facade(self, rng):
        return mix((26, 22, 58), (42, 32, 76), rng.random())

    def window_colors(self, rng):
        warm = [(255, 214, 132), (255, 190, 104), (248, 160, 92)]
        cool = [(168, 214, 255), (132, 186, 240)]
        pool = warm * 3 + cool if rng.random() < 0.7 else cool * 2 + warm
        return pool

    def building(self, L, rng, x, w, top, base, style):
        f = self.facade(rng)
        tiers = [(x, w, top)]
        if style in ("deco", "stepped"):
            step = 5 if style == "deco" else 7
            hh = 18 if style == "deco" else 26
            tiers = [(x, w, top + 2 * hh), (x + step, w - 2 * step, top + hh), (x + 2 * step, w - 4 * step, top)]
        for tx, tw, tt in tiers:
            for y in range(tt, base):
                k = (y - tt) / max(1, base - tt)
                c = mix(mix(f, (70, 52, 104), 0.35), f, min(1, k * 2.2))
                L.fill(tx, y, tx + tw, y + 1, c)
            L.fill(tx + tw - 3, tt, tx + tw, base, shade(f, 0.72))       # side face in shadow
            L.fill(tx, tt, tx + 1, base, mix(f, SUN_DIR_RIM, 0.42))      # sun-side rim
            L.fill(tx, tt, tx + tw, tt + 1, mix(f, (150, 90, 130), 0.5))  # cap
        pool = self.window_colors(rng)
        lit_p = rng.uniform(0.32, 0.55)
        for tx, tw, tt in tiers:
            if style == "low":
                self.windows_grid(L, rng, tx, tw, tt, base, f, pool, 0.5, (2, 2), (4, 4))
            elif style == "strip":
                for y in range(tt + 4, base - 2, 5):
                    xx = tx + 3
                    while xx < tx + tw - 4:
                        seg = rng.randint(3, 9)
                        on = rng.random() < lit_p
                        c = rng.choice(pool) if on else shade(f, 1.45)
                        L.fill(xx, y, min(tx + tw - 4, xx + seg), y + 2, c)
                        xx += seg + 1
            elif style == "glass":
                for y in range(tt + 2, base):
                    sc = sky_color(max(0, y - 90))
                    c = mix(f, sc, 0.28)
                    L.fill(tx + 1, y, tx + tw - 3, y + 1, c)
                for xx in range(tx + 3, tx + tw - 3, 4):
                    L.fill(xx, tt + 2, xx + 1, base, shade(f, 0.85))
                g0 = tx + tw // 3
                for y in range(tt + 2, base):
                    gx = g0 + (y - tt) // 3 - 6
                    for k in range(3):
                        if tx + 2 <= gx + k < tx + tw - 3 and ((y // 2) % 9) < 6:
                            L.put(gx + k, y, mix(f, (210, 160, 190), 0.33))
                for y in range(tt + 5, base, 6):
                    if rng.random() < 0.45:
                        a = rng.randint(tx + 2, tx + tw // 2)
                        b = rng.randint(a + 3, tx + tw - 4)
                        L.fill(a, y, b, y + 2, rng.choice(pool))
            else:
                size, gap = ((2, 2), (4, 5)) if style != "deco" else ((1, 3), (3, 5))
                self.windows_grid(L, rng, tx, tw, tt, base, f, pool, lit_p, size, gap)
        if style == "spire":
            sx = x + w // 2
            L.fill(sx - 1, top - 40, sx + 1, top, (60, 46, 90))
            L.fill(sx - 3, top - 8, sx + 3, top, (50, 40, 80))
            self.blinkers.append((sx - 1, top - 41, (255, 64, 64), 40, rng.random(), 0.3, 5))
            self.blinkers.append((sx - 1, top - 20, (255, 64, 64), 40, rng.random(), 0.3, 3))
            for k in range(3):
                L.fill(x + 2 + k * 2, top + 2 + k * 2, x + w - 2 - k * 2, top + 3 + k * 2, (110, 80, 130))
        elif style in ("deco", "stepped"):
            cx = x + w // 2
            L.fill(cx - 1, top - 14, cx + 1, top, (70, 52, 100))
            self.blinkers.append((cx - 1, top - 15, (255, 70, 70), 30, rng.random(), 0.35, 3))
        elif style != "low" and rng.random() < 0.7:
            ax = rng.randint(x + 3, x + w - 6)
            ah = rng.randint(5, 16)
            L.fill(ax, top - ah, ax + 1, top, (60, 46, 90))
            self.blinkers.append((ax, top - ah - 1, (255, 70, 70), rng.choice([24, 30, 40]), rng.random(), 0.3, 3))
            if rng.random() < 0.6:                      # water tower or box on the roof
                bx = rng.randint(x + 2, x + w - 10)
                L.fill(bx, top - 6, bx + 7, top - 2, (48, 36, 74))
                L.fill(bx + 1, top - 2, bx + 2, top, (40, 30, 64))
                L.fill(bx + 5, top - 2, bx + 6, top, (40, 30, 64))
                L.fill(bx, top - 7, bx + 7, top - 6, (90, 60, 110))

    def windows_grid(self, L, rng, tx, tw, tt, base, f, pool, lit_p, size, gap):
        ww, wh = size
        gx, gy = gap
        off = shade(f, 1.35)
        cols = list(range(tx + 3, tx + tw - 3 - ww + 1, gx))
        for y in range(tt + 4, base - wh, gy):
            row_lit = rng.random() < 0.85
            for xx in cols:
                on = row_lit and rng.random() < lit_p
                c = rng.choice(pool) if on else off
                L.fill(xx, y, xx + ww, y + wh, c)
                if rng.random() < 0.025:                 # someone turns the light on or off during the loop
                    a = rng.random()
                    self.toggles.append((xx, y, xx + ww, y + wh, rng.choice(pool), off, a, (a + rng.uniform(0.15, 0.5)) % 1))

    def landing_tower(self, L, rng):
        x0, x1 = TOWER
        f = (34, 28, 70)
        for y in range(ROOF_Y, 472):
            k = (y - ROOF_Y) / (472 - ROOF_Y)
            L.fill(x0, y, x1, y + 1, mix(mix(f, (72, 54, 108), 0.4), f, min(1, k * 2.4)))
        L.fill(x1 - 4, ROOF_Y, x1, 472, shade(f, 0.72))
        L.fill(x0, ROOF_Y, x0 + 1, 472, mix(f, SUN_DIR_RIM, 0.45))
        L.fill(x0 - 1, ROOF_Y, x1 + 1, ROOF_Y + 2, (120, 90, 140))          # parapet
        L.fill(x0 - 1, ROOF_Y + 2, x1 + 1, ROOF_Y + 3, (60, 44, 88))
        for x in range(x0 + 2, x1, 5):                                        # roof edge lights
            L.put(x, ROOF_Y, (255, 236, 190) if (x // 5) % 2 else (150, 220, 255))
        # stairwell box with a door and a small window
        sx0, sx1, stop = STAIR
        L.fill(sx0, stop, sx1, ROOF_Y, (52, 42, 86))
        L.fill(sx0, stop, sx1, stop + 2, (110, 84, 136))
        L.fill(sx0, stop, sx0 + 1, ROOF_Y, mix((52, 42, 86), SUN_DIR_RIM, 0.45))
        L.fill(sx1 - 3, stop, sx1, ROOF_Y, (38, 30, 64))
        d0, dy0, d1, dy1 = DOOR_R
        L.fill(d0 - 1, dy0 - 1, d1 + 1, dy1, (30, 24, 50))                    # frame
        self.stair_window = (576, 316, 580, 320)
        L.fill(*self.stair_window, (30, 26, 52))
        # antenna and AC unit on the left side of the roof
        L.fill(472, ROOF_Y - 28, 473, ROOF_Y, (70, 54, 100))
        L.fill(470, ROOF_Y - 20, 475, ROOF_Y - 19, (70, 54, 100))
        self.blinkers.append((472, ROOF_Y - 29, (255, 64, 64), 30, 0.25, 0.3, 4))
        L.fill(478, ROOF_Y - 6, 488, ROOF_Y, (64, 50, 92))
        L.fill(478, ROOF_Y - 6, 488, ROOF_Y - 5, (120, 92, 140))
        for x in range(480, 487, 2):
            L.fill(x, ROOF_Y - 4, x + 1, ROOF_Y - 1, (40, 32, 64))
        # facade windows; the right column stays dark: it is the staircase Claude walks down
        pool = [(255, 214, 132), (255, 190, 104), (168, 214, 255)]
        self.stair_col = []
        L.fill(x1 - 13, 349, x1 - 5, 470, (24, 20, 48))                     # dark stair shaft
        L.fill(x1 - 13, 349, x1 - 12, 470, (44, 36, 76))
        for y in range(352, 466, 6):
            for i, xx in enumerate(range(x0 + 4, x1 - 8, 5)):
                stair = xx >= x1 - 12
                if stair:
                    L.fill(xx, y, xx + 3, y + 4, (40, 34, 72))
                    self.stair_col.append((xx, y))
                    continue
                on = rng.random() < 0.42
                L.fill(xx, y, xx + 3, y + 3, rng.choice(pool) if on else (52, 44, 92))
        # neon CLAUDE sign
        m = text_mask("CLAUDE", FONT5)
        h, w = m.shape
        nx, ny = (x0 + x1) // 2 - w // 2, 338
        self.neons.append(dict(mask=m, x=nx, y=ny, color=(255, 150, 84), dim=(120, 60, 50), flicker=(3, 4), seed=1))
        L.fill(nx - 3, ny - 2, nx + w + 3, ny + h + 2, (26, 20, 46))

    # ------------------------------------------------------------------------------------------ street level
    def build_near(self):
        L = Layer(self.H, self.W)
        G = np.zeros((self.H, self.W, 3), np.float32)          # static additive light
        self.near, self.near_glow = L, G
        rng = self.rng
        th = bayer(self.H, self.W)
        shops = [  # x0, x1, top, sign, sign color
            (-4, 60, 446, "MOTEL", (255, 92, 168)), (60, 118, 456, "BAR", (90, 230, 255)),
            (118, 190, 440, "PIZZA", (255, 180, 70)), (190, 246, 452, "24/7", (110, 255, 140)),
            (246, 318, 444, "CAFE", (255, 120, 120)), (318, 380, 454, None, None),
            (380, 452, 448, "BAR", (190, 120, 255)), (452, 520, 456, None, None),
            (520, 604, 438, "HOTEL", (255, 214, 120)), (604, 668, 450, "PIZZA", (90, 230, 255)),
            (668, 744, 442, "MOTEL", (255, 92, 168)), (744, 800, 454, "CAFE", (255, 180, 70)),
            (800, 868, 446, "24/7", (110, 255, 140)),
        ]
        for x0, x1, top, sign, sc in shops:
            f = mix((18, 14, 36), (30, 22, 50), rng.random())
            L.fill(x0, top, x1, SIDEWALK_Y, f)
            L.fill(x0, top, x1, top + 1, mix(f, (120, 80, 130), 0.5))
            L.fill(x0, top, x0 + 1, SIDEWALK_Y, mix(f, SUN_DIR_RIM, 0.3))
            # upper windows
            for y in range(top + 5, 466, 7):
                for xx in range(x0 + 4, x1 - 4, 7):
                    on = rng.random() < 0.45
                    L.fill(xx, y, xx + 4, y + 3, rng.choice([(255, 206, 128), (255, 170, 100), (160, 200, 255)]) if on
                           else shade(f, 1.4))
            # shop front: awning stripes and a lit window
            aw = rng.choice([((220, 70, 120), (240, 220, 230)), ((40, 160, 170), (230, 240, 240)),
                             ((230, 140, 60), (250, 230, 200))])
            for xx in range(x0 + 2, x1 - 2):
                L.put(xx, 469, aw[(xx // 3) % 2])
                L.put(xx, 470, shade(aw[(xx // 3) % 2], 0.7))
            win = (x0 + 5, 473, x1 - 5, 483)
            if sign == "HOTEL":
                win = (x0 + 5, 473, LOBBY_DOOR[0] - 3, 483)
                L.fill(LOBBY_DOOR[2] + 3, 473, x1 - 5, 483, (255, 200, 120))
                L.fill(LOBBY_DOOR[0] - 1, LOBBY_DOOR[1] - 1, LOBBY_DOOR[2] + 1, LOBBY_DOOR[3], (60, 44, 70))
                L.fill(*LOBBY_DOOR, (120, 96, 110))
            wc = rng.choice([(255, 200, 120), (255, 170, 110), (200, 230, 255)])
            L.fill(*win, wc)
            for xx in range(win[0] + 6, win[2], 9):
                L.fill(xx, win[1], xx + 1, win[3], shade(f, 1.2))
            G[484:490, max(0, win[0] - 2):min(self.W, win[2] + 2)] += np.array(wc, np.float32) * 0.12
            if sign:
                m = text_mask(sign, FONT3)
                h, w = m.shape
                nx, ny = (x0 + x1) // 2 - w // 2, top + 3 if sign != "HOTEL" else top - 8
                if sign == "HOTEL":            # a sign on posts above the lobby roof
                    L.fill(nx - 3, ny - 2, nx + w + 3, ny + h + 2, (30, 22, 44))
                    L.fill(nx - 1, ny + h + 2, nx, top, (50, 40, 64))
                    L.fill(nx + w, ny + h + 2, nx + w + 1, top, (50, 40, 64))
                self.neons.append(dict(mask=m, x=nx, y=ny, color=sc, dim=shade(sc, 0.35),
                                       flicker=(rng.randrange(len(sign)), rng.choice([2, 3, 4])) if rng.random() < 0.5 else None,
                                       seed=rng.randrange(1000)))
                # neon reflection on the wet road: vertical streaks under the lit columns, fading with distance
                cols = np.where(m.any(axis=0))[0] + nx
                r0, r1 = SIDEWALK_Y + 6, LANE_LO + 1
                for yy in range(r0, r1):
                    k = 1 - (yy - r0) / (r1 - r0)
                    for cx_ in cols:
                        if 0 <= cx_ < self.W and th[yy, cx_] < 0.55 * k + 0.1 and (yy + cx_) % 3:
                            G[yy, cx_] += np.array(sc, np.float32) * 0.16 * k
        # palms in front of the shops
        self.palms = []
        for px, hgt, lean in ((26, 78, 6), (138, 64, -4), (262, 84, 8), (402, 70, -5), (612, 80, 5),
                              (718, 66, -6), (846, 74, 4)):
            self.palms.append(self.palm(px, hgt, lean))
        # street lamps
        for lx in range(52, self.W, 104):
            L.fill(lx, 456, lx + 1, SIDEWALK_Y + 2, (40, 34, 56))
            L.fill(lx, 456, lx + 5, 457, (40, 34, 56))
            L.fill(lx + 3, 457, lx + 6, 458, (255, 230, 170))
            a = halo(16, 0.30, 4)
            ys, xs = slice(457 - 16, 457 + 17), slice(lx + 4 - 16, lx + 4 + 17)
            if xs.start >= 0 and xs.stop <= self.W:
                G[ys, xs] += a[..., None] * np.array((255, 200, 130), np.float32)
            G[SIDEWALK_Y:SIDEWALK_Y + 6, max(0, lx - 10):lx + 18] += np.array((255, 200, 130), np.float32) * 0.16
        # sidewalk, curb, road with lane marks, far sidewalk and railing
        L.fill(0, SIDEWALK_Y, self.W, SIDEWALK_Y + 5, (64, 46, 74))
        for xx in range(0, self.W, 8):
            L.fill(xx, SIDEWALK_Y, xx + 1, SIDEWALK_Y + 5, (52, 38, 64))
        L.fill(0, SIDEWALK_Y + 5, self.W, SIDEWALK_Y + 6, (118, 90, 120))
        L.fill(0, SIDEWALK_Y + 6, self.W, LANE_LO + 2, (34, 28, 48))
        for xx in range(0, self.W, 12):
            L.fill(xx, LANE_UP + 1, xx + 6, LANE_UP + 2, (190, 160, 90))
        for y in range(SIDEWALK_Y + 6, LANE_LO + 2):
            for xx in range(self.W):
                if th[y, xx] < 0.06:
                    L.put(xx, y, (44, 36, 60))
        L.fill(0, LANE_LO + 2, self.W, SEAWALL - 1, (70, 52, 80))
        L.fill(0, SEAWALL - 1, self.W, SEAWALL, (40, 30, 54))
        for xx in range(0, self.W, 6):
            L.fill(xx, SEAWALL - 5, xx + 1, SEAWALL - 1, (90, 70, 100))
        L.fill(0, SEAWALL - 5, self.W, SEAWALL - 4, (110, 84, 120))
        self.near_glow = np.minimum(G, 255)
        rows = np.where(self.near_glow.sum(axis=(1, 2)) > 0)[0]
        self.near_glow_rows = (int(rows[0]), int(rows[-1]) + 1)

    def palm(self, x, hgt, lean):
        """Pre-rendered sway frames of a palm: list of Sprite plus where its trunk base sits."""
        frames = []
        trunk_c, frond_c, rim_c = (24, 16, 38), (20, 18, 40), (128, 64, 92)
        w, h = 70, hgt + 20
        bx = 35
        for f in range(6):
            sway = math.sin(f / 6 * 2 * math.pi) * 1.2
            cv = np.zeros((h, w, 3), np.uint8)
            m = np.zeros((h, w), bool)
            def P(px, py, c):
                px, py = int(round(px)), int(round(py))
                if 0 <= px < w and 0 <= py < h:
                    cv[py, px] = c
                    m[py, px] = True
            tx, ty = bx, h - 1
            top = None
            for i in range(hgt):
                u = i / hgt
                px = bx + lean * u * u + sway * u ** 3
                py = h - 1 - i
                P(px, py, trunk_c)
                P(px + 1, py, trunk_c)
                if i % 4 == 0:
                    P(px - 1, py, rim_c)
                top = (px, py)
            cx, cy = top
            for k, (ang, ln) in enumerate(((-2.7, 22), (-2.2, 24), (-1.6, 18), (-1.0, 22), (-0.45, 24), (0.15, 20),
                                           (-0.1, 14), (-2.95, 16))):
                a = ang + sway * 0.04
                for s in range(ln):
                    u = s / ln
                    px = cx + math.cos(a) * s
                    py = cy + math.sin(a) * s + (u * u) * ln * 0.55
                    P(px, py, frond_c)
                    if s % 2 == 0 and s > 3:
                        P(px, py + 1, frond_c)
                        if s % 4 == 0:
                            P(px + (1 if math.cos(a) > 0 else -1), py + 2, frond_c)
                    if s % 2 == 0 and s < ln * 0.8:
                        P(px, py - 1, rim_c if math.cos(a) < 0.2 else (70, 40, 74))
            P(cx, cy, (60, 40, 50))
            P(cx + 1, cy + 1, (60, 40, 50))
            frames.append(Sprite(cv, m))
        return dict(frames=frames, x=x - bx, y=SIDEWALK_Y + 1 - h, cycles=self.rng.choice([10, 12, 15]), phase=self.rng.random())

    # ------------------------------------------------------------------------------------------ searchlights
    def build_beams(self):
        h = 440
        yy, xx = np.mgrid[0:h, 0:self.W].astype(np.float32)
        self.beams = []
        for ox, oy, base, amp, n, ph in ((120, 430, -0.42, 0.30, 2, 0.1), (752, 440, 0.38, 0.26, 3, 0.55)):
            dx, dy = xx - ox, oy - yy
            ang = np.arctan2(dx, np.maximum(dy, 0.01)).astype(np.float32)
            dist = np.sqrt(dx * dx + dy * dy).astype(np.float32)
            fade = np.clip(1 - dist / 560, 0, 1).astype(np.float32)
            self.beams.append(dict(ang=ang, fade=fade, valid=(dy > 0), base=base, amp=amp, n=n, ph=ph))
        self.beam_th = bayer(h, self.W)
        # beams dissolve into the haze before they reach the band behind the menu bar
        self.beam_top = np.clip((yy - STILL_TOP) / 40.0, 0, 1).astype(np.float32)

    # ------------------------------------------------------------------------------------------ water
    def build_water(self):
        H, W = self.H, self.W
        rows = []
        for y in range(SEAWALL, H):
            k = (y - SEAWALL) / max(1, H - SEAWALL)
            rows.append(mix((70, 34, 86), (14, 12, 38), k ** 0.7))
        self.water_rows = np.array(rows, np.uint8)
        rng = self.rng
        self.ripples = [(rng.randrange(W), rng.randrange(SEAWALL + 1, H), rng.randint(3, 10), rng.randint(3, 8),
                         rng.random()) for _ in range(150)]
        lights = [(52 + 104 * i + 4, (255, 200, 130)) for i in range(9)]
        lights += [(n["x"] + n["mask"].shape[1] // 2, n["color"]) for n in self.neons if n["y"] > 400]
        self.reflections = lights

    # ------------------------------------------------------------------------------------------ per-frame parts
    def draw_stars(self, out, t):
        tw = 0.6 + 0.4 * np.sin(2 * np.pi * (t * self.star_n / LOOP + self.star_ph))
        tw = np.where(self.star_xy[0] < STILL_TOP, 0.6, tw)          # behind the menu bar: still
        a = np.clip(self.star_b * tw, 0, 1)[:, None]
        out[self.star_xy] = (self.star_base * (1 - a) + self.star_col * a).astype(np.uint8)
        for x, y, n, ph in self.bright_stars:
            s = 0.55 + 0.45 * wave(t, n, ph)
            base = self.sky_rgb[y, x].astype(np.float32)
            put(out, x, y, tuple((base + (np.array((255, 250, 235)) - base) * s).astype(int)))
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                b2 = self.sky_rgb[y + dy, x + dx].astype(np.float32)
                put(out, x + dx, y + dy, tuple((b2 + (np.array((220, 220, 255)) - b2) * s * 0.45).astype(int)))

    def draw_clouds(self, out, t):
        for period, y0, rgb, mask in self.cloud_layers:
            off = int(cyc(t, 1) * period)
            idx = (np.arange(self.W) - off) % period
            r, m = rgb[:, idx], mask[:, idx]
            h = r.shape[0]
            np.copyto(out[y0:y0 + h], r, where=m[:, :, None])

    def draw_beams(self, out, t):
        h = self.beam_th.shape[0]
        reg = out[:h].astype(np.float32)
        for b in self.beams:
            theta = b["base"] + b["amp"] * math.sin(2 * math.pi * cyc(t, b["n"], b["ph"]))
            d = np.abs(b["ang"] - theta)
            core = np.clip(1.25 - d / 0.036, 0, 1)                   # flat core, short soft edge
            a = core * b["fade"] * b["valid"] * self.beam_top
            lv = np.floor(a * 3 + (self.beam_th - 0.5) * 0.35 + 0.5)  # three flat bands, dithered only at seams
            reg += (np.clip(lv, 0, 3) / 3 * 0.17)[..., None] * np.array((255, 236, 214), np.float32)
        out[:h] = np.minimum(reg, 255).astype(np.uint8)

    def draw_blinkers(self, out, t, layer):
        for x, y, c, n, ph, duty, gr in self.blinker_layers[layer]:
            if cyc(t, n, ph) < duty * 0.5:
                put(out, x, y, (255, 230, 230))
                put(out, x + 1, y, c)
                glow(out, x, y, gr, c, 0.5, 3)
            else:
                put(out, x, y, shade(c, 0.45))

    def draw_toggles(self, out, t):
        p = cyc(t, 1)
        for x0, y0, x1, y1, on, off, a, b in self.toggles:
            lit = (a <= p < b) if a < b else (p >= a or p < b)
            fill(out, x0, y0, x1, y1, on if lit else off)

    def draw_neons(self, out, t):
        for n in self.neons:
            m, x, y = n["mask"], n["x"], n["y"]
            lit = m
            if n["flicker"] is not None:
                letter, cycles = n["flicker"]
                p = cyc(t, cycles, n["seed"] / 1000)
                if 0.62 < p < 0.64 or 0.66 < p < 0.665 or 0.70 < p < 0.74:
                    lit = m.copy()
                    gw = len(FONT5["C"][0]) if m.shape[0] == 7 else 3
                    lit[:, letter * (gw + 1):letter * (gw + 1) + gw] = False
            dim = m & ~lit
            h, w = m.shape
            reg = out[y:y + h, x:x + w]
            reg[lit] = n["color"]
            reg[dim] = n["dim"]
            ga = n.get("_ga")
            if ga is None:
                ga = n["_ga"] = neon_glow_alpha(m, 6)
            add_light(out, x - 6, y - 6, ga * (lit.sum() / max(1, m.sum())), n["color"])

    def draw_palms(self, out, t):
        for p in self.palms:
            ph = cyc(t, p["cycles"], p["phase"])
            spr = p["frames"][int(ph * len(p["frames"])) % len(p["frames"])]
            blit(out, spr, p["x"], p["y"])

    def draw_water(self, out, t):
        H, W = self.H, self.W
        out[SEAWALL:H] = self.water_rows[:, None, :]
        for x, y, ln, n, ph in self.ripples:
            dx = int(4 * math.sin(2 * math.pi * cyc(t, n, ph)))
            c = tuple(min(255, v + 30) for v in self.water_rows[y - SEAWALL])
            fill(out, x + dx, y, x + dx + ln, y + 1, c)
        for lx, c in self.reflections:
            for y in range(SEAWALL + 1, H, 2):
                k = 1 - (y - SEAWALL) / (H - SEAWALL)
                wob = int(2 * math.sin(2 * math.pi * cyc(t, 12, y / 37.0)))
                w = 1 + int(3 * k)
                reg = out[y, max(0, lx + wob - w):min(W, lx + wob + w)].astype(np.float32)
                out[y, max(0, lx + wob - w):min(W, lx + wob + w)] = np.minimum(
                    reg + np.array(c, np.float32) * 0.35 * k, 255).astype(np.uint8)

    def draw_bridge_traffic(self, out, t):
        x0, x1, deck = self.bridge_deck
        span_ = x1 - x0
        for i in range(7):
            x = x0 + (cyc(t, 3, i / 7) * span_)
            put(out, int(x), deck + 1, (255, 246, 210))
        for i in range(6):
            x = x1 - (cyc(t, 2, i / 6 + 0.05) * span_)
            put(out, int(x), deck + 2, (255, 70, 60))


# ============================================================================================ the story
WIND = 11.0


class Story:
    """The one-shot timeline: helicopter, jump, parachute, landing, stairs, taxi. Positions in art pixels."""

    T_JUMP, T_PILOT, T_OPEN, T_LAND = 8.0, 8.95, 9.7, 28.0

    def __init__(self):
        self.land_x, self.land_y = 500, ROOF_Y - 1
        self.simulate_freefall()
        s_land = self.sway_px(self.T_LAND)
        self.keys = [
            (self.T_OPEN, self.p_open[0], self.p_open[1], self.v_open[0], self.v_open[1]),
            (15.6, 366, 222, 8.5, 8.2),
            (21.0, 424, 269, 12.5, 8.0),
            (25.6, 478, 309, 9.5, 6.6),
            (self.T_LAND, self.land_x - 0.75 * s_land, self.land_y, 3.0, 2.0),
        ]

    # --------------------------------------------------------------------------------------- helicopter
    def heli_x(self, t):
        if t < 6.0:
            return lerp(-120, 216, ease_out(t / 6.0) * 0.97 + (t / 6.0) * 0.03)
        if t < 8.4:
            return 216 + 2.5 * (t - 6.0)
        tt = t - 8.4
        return 222 + 2.5 * tt + 0.5 * 34 * tt * tt

    def heli_y(self, t):
        if t < 6.0:
            y = lerp(60, 72, smooth(t / 6.0))
        elif t < 8.4:
            y = 72
        else:
            y = 72 - 0.5 * 2.4 * (t - 8.4) ** 2
        return y + 1.0 * math.sin(t * 2.3)

    def heli_tilt(self, t):
        if t < 3.0:
            return 1
        if t < 6.0:
            return -2 if t < 5.2 else -1
        if t < 8.6:
            return 0
        return min(3, 1 + int((t - 8.6) * 1.2))

    def heli_state(self, t):
        x = self.heli_x(t)
        if x > ART_W + 60 or x < -140:
            return None
        return dict(x=int(round(x)), y=int(round(self.heli_y(t))), tilt=self.heli_tilt(t), rotor=t * 2 * math.pi * 7.3)

    def door_world(self, t):
        """World position of the door's bottom middle at time t (where Claude's feet are inside)."""
        st = self.heli_state(t) or dict(x=int(round(self.heli_x(t))), y=int(round(self.heli_y(t))), tilt=self.heli_tilt(t))
        _, _, offs = heli(st["tilt"])
        dx = (DOOR[0] + DOOR[2]) // 2
        return st["x"] + dx, st["y"] + DOOR[3] - 1 + offs[dx]

    # --------------------------------------------------------------------------------------- jump physics
    def simulate_freefall(self):
        dt = 1 / 480
        x, y = self.door_world(self.T_JUMP)
        vx = (self.heli_x(self.T_JUMP + 0.01) - self.heli_x(self.T_JUMP)) / 0.01 + 20
        vy = -38.0
        self.ff = []
        t = self.T_JUMP
        while t <= self.T_OPEN + 1e-9:
            self.ff.append((t, x, y))
            k = 0.8 + 18.0 * smooth(span(t, self.T_PILOT + 0.15, self.T_OPEN - 0.1))
            ax = -k * 0.25 * (vx - WIND) if t > self.T_PILOT else -0.6 * (vx - WIND)
            ay = 170 - k * vy
            vx += ax * dt
            vy += ay * dt
            x += vx * dt
            y += vy * dt
            t += dt
        self.p_open = (x, y)
        self.v_open = (vx, vy)

    def ff_pos(self, t):
        i = min(len(self.ff) - 1, max(0, int((t - self.T_JUMP) * 480)))
        return self.ff[i][1], self.ff[i][2]

    # --------------------------------------------------------------------------------------- sway
    def sway_phi(self, t):
        if t < self.T_OPEN - 0.3:
            return 0.0
        tt = t - (self.T_OPEN - 0.3)
        amp = 0.06 + 0.26 * math.exp(-tt / 2.6)
        phi = amp * math.sin(2 * math.pi * 0.42 * tt)
        phi += 0.10 * math.sin(math.pi * span(t, 18.0, 23.0))          # banking through the S-turn
        phi += 0.16 * smooth(span(t, 26.6, self.T_LAND))                  # flare: legs swing forward
        return phi

    def sway_px(self, t):
        return 22 * math.sin(self.sway_phi(t))

    # --------------------------------------------------------------------------------------- Claude
    def claude_state(self, t):
        """None when off stage, else dict(pos, sprite args, canopy, lines, clip, extras)."""
        st = dict(arms="down", legs="stand", eyes="normal", squash=0, mouth=False, bundle=False, clip=None,
                  canopy=None, lines=[], pilot=None, speed=0.0, tint=0.0, layer="sky")
        blink = any(a <= t < a + 0.12 for a in (3.1, 6.4, 13.2, 17.9, 22.6, 26.0, 31.9, 36.4, 47.2))
        if t < self.T_JUMP:
            hs = self.heli_state(t)
            if hs is None:
                return None
            spr, lo, offs = heli(hs["tilt"])
            dx = (DOOR[0] + DOOR[2]) // 2
            x0 = hs["x"] + DOOR[0]
            y0 = hs["y"] + lo + DOOR[1] + offs[dx] - lo
            st["clip"] = (x0, y0, x0 + DOOR[2] - DOOR[0], y0 + DOOR[3] - DOOR[1])
            fx, fy = hs["x"] + dx, y0 + DOOR[3] - DOOR[1] - 1
            st["eyes"] = "down" if 4.2 < t < 6.8 else ("blink" if blink else "normal")
            if t > 7.1:
                st.update(squash=2, legs="squash", arms="down", eyes="normal")
            st["pos"] = (fx, fy)
            st["layer"] = "heli"
            return st
        if t < self.T_OPEN:
            fx, fy = self.ff_pos(t)
            st["pos"] = (fx, fy)
            st["speed"] = abs(self.ff[min(len(self.ff) - 1, int((t - self.T_JUMP) * 480))][2] -
                              self.ff[max(0, int((t - self.T_JUMP) * 480) - 48)][2]) * 10
            if t < self.T_JUMP + 0.18:
                st.update(squash=-1, arms="cheer", legs="stand", eyes="wide")
            elif t < self.T_PILOT + 0.3:
                st.update(arms="spread", legs="spread", eyes="wide", mouth=True)
            else:
                st.update(arms="hold", legs="a", eyes="wide")
            self.rig(st, t)
            return st
        if t < self.T_LAND:
            px, py = path(t, self.keys)
            s = self.sway_px(t)
            fx = px + 0.75 * s
            fy = py - 22 * (1 - math.cos(self.sway_phi(t))) * 0.5
            st["pos"] = (fx, fy)
            kick = int(t * 2.2) % 2
            st.update(arms="hold", legs="a" if kick else "b", eyes="blink" if blink else ("happy" if 12.2 < t < 13.0 else "normal"))
            if t > self.T_LAND - 0.5:
                st["legs"] = "stand"
            self.rig(st, t)
            return st
        return self.after_landing(st, t, blink)

    def rig(self, st, t):
        """Canopy and lines for the freefall/deploy/descent phases."""
        fx, fy = st["pos"]
        _, ax, ay = claude(st["arms"], st["legs"], st["eyes"], st["squash"], st["mouth"], st["bundle"])
        hands = [(fx + hx, fy + hy) for hx, hy in claude_hands(st["arms"])]
        top_y = fy - 13 - 2
        if t < self.T_PILOT:
            return
        if t < self.T_PILOT + 0.18:                      # pilot chute out
            u = span(t, self.T_PILOT, self.T_PILOT + 0.18)
            px, py = fx - 2, top_y - 4 - 18 * u
            st["pilot"] = (int(px), int(py))
            st["lines"].append(((fx, top_y), (px + 2, py + 3), 0))
            return
        u = span(t, self.T_PILOT + 0.18, self.T_OPEN + 0.35)
        if u < 0.45:
            inf = u / 0.45 * 0.55
        elif u < 0.8:
            inf = 0.55 + (u - 0.45) / 0.35 * 0.55
        else:
            inf = 1.1 - (u - 0.8) / 0.2 * 0.1
        w = int(round(lerp(7, 45, clamp(inf, 0, 1.2))))
        B = lerp(20, 16, clamp(inf)) if inf <= 1 else 16 - (inf - 1) * 12
        C = lerp(2, 12, clamp(inf))
        Y1 = lerp(17, 11, clamp(inf))
        s = self.sway_px(t)
        tilt = int(round(-2.2 * math.sin(self.sway_phi(t)) * 4))
        spr, attach = canopy(w, round(B * 2) / 2, round(C), round(Y1), tilt)
        cx = fx - s if t >= self.T_OPEN else fx
        drop = lerp(14, 11, clamp(inf))
        tip_y = min(hands[0][1], hands[3][1]) - drop
        gx = int(round(cx - spr.w / 2))
        gy = int(round(tip_y - attach[0][1]))
        st["canopy"] = (spr, gx, gy)
        pts = [(gx + a[0], gy + a[1] + 1) for a in attach]
        st["lines"] += [(pts[0], hands[0], 0), (pts[1], hands[1], 0), (pts[2], hands[2], 0), (pts[3], hands[3], 0)]

    def after_landing(self, st, t, blink):
        lx, ly = self.land_x, self.land_y
        door_cx = (DOOR_R[0] + DOOR_R[2]) // 2
        # canopy collapsing forward onto the roof, then a pile, then gathered into a bundle
        if t < 29.4:
            # 28.0-28.7 the canopy flies on and tips forward; 28.7-29.4 it deflates and drapes onto the roof
            s0 = self.sway_px(self.T_LAND)
            hands = [(lx + hx, ly + hy) for hx, hy in claude_hands("hold" if t < 28.6 else "down")]
            tip_y0 = hands[0][1] - 11
            if t < 28.7:
                u = smooth(span(t, self.T_LAND, 28.7))
                spr, attach = canopy(45, round(lerp(16, 12.5, u) * 2) / 2, 12, 11, int(round(lerp(0, 4, u))))
                cx, tip_y, sag = lerp(lx - s0, lx + 14, u), tip_y0 + 7 * u, 2 * u
                lines = (0, 1, 2, 3)
            else:
                u = smooth(span(t, 28.7, 29.4))
                spr, attach = canopy(int(lerp(45, 39, u)), round(lerp(12.5, 3.5, u) * 2) / 2, round(lerp(12, 1, u)),
                                     round(lerp(11, 3, u)), int(round(lerp(4, 1, u))), flat=u > 0.6)
                cx, tip_y, sag = lerp(lx + 14, lx + 28, u), lerp(tip_y0 + 7, ly, u ** 1.4), lerp(2, 5, u)
                lines = (0, 1)                       # only the near lines stay visible, lying slack on the roof
            gx = int(round(cx - spr.w / 2))
            gy = int(round(tip_y - attach[3][1]))
            st["canopy"] = (spr, gx, gy)
            pts = [(gx + a[0], gy + a[1] + 1) for a in attach]
            st["lines"] = [(pts[i], hands[i], sag) for i in lines]
        elif t < 32.8:
            st["pile"] = (lx + 9, ly - 5, 1.0)
        elif t < 34.2:
            st["pile"] = (None, ly - 5, 1 - span(t, 32.8, 34.2))
        # Claude
        if t < self.T_LAND + 0.25:
            st.update(pos=(lx, ly), squash=2, legs="squash", arms="hold", eyes="blink")
            return st
        if t < 29.6:
            st.update(pos=(lx, ly), arms="down" if t > 28.6 else "hold", eyes="blink" if blink else "normal")
            return st
        if t < 31.8:
            hop = 0
            for h0 in (29.75, 30.6):
                if h0 <= t < h0 + 0.5:
                    hop = 5 * math.sin(math.pi * (t - h0) / 0.5)
                    st["legs"] = "tuck" if hop > 2 else "stand"
            st.update(pos=(lx, ly - int(round(hop))), arms="cheer" if t < 31.3 else "down", eyes="happy")
            return st
        if t < 32.8:
            x = lerp(lx, lx + 12, span(t, 31.8, 32.8))
            st.update(pos=(x, ly - self.bob(t)), legs=self.walk(t), eyes="blink" if blink else "normal")
            return st
        if t < 34.2:
            st.update(pos=(lx + 12, ly), arms="down", eyes="down")
            st["pile"] = (lx + 12, ly - 5, 1 - span(t, 32.8, 34.2))
            return st
        if t < 34.6:
            u = span(t, 34.2, 34.6)
            st.update(pos=(lx + 12, ly), arms="carry", eyes="happy", bundle=True, squash=1 if u < 0.4 else 0)
            st["pile"] = None
            return st
        if t < 39.0:
            x = lerp(lx + 12, door_cx, span(t, 34.6, 39.0))
            st.update(pos=(x, ly - self.bob(t)), arms="carry", bundle=True, legs=self.walk(t),
                      eyes="blink" if blink else "normal")
            return st
        if t < 39.7:
            u = span(t, 39.0, 39.7)
            st.update(pos=(door_cx, ly - int(u * 2)), arms="carry", bundle=True, legs=self.walk(t), tint=0.25 + 0.75 * u,
                      clip=DOOR_R, layer="door")
            return st
        # street: out of the lobby, into the taxi
        if 46.0 <= t < 48.3:
            dcx = (LOBBY_DOOR[0] + LOBBY_DOOR[2]) // 2
            fy = SIDEWALK_Y + 2
            if t < 46.5:
                u = span(t, 46.0, 46.5)
                st.update(pos=(dcx, fy), arms="carry", bundle=True, legs=self.walk(t), tint=1 - u, clip=LOBBY_DOOR,
                          layer="street")
                return st
            if t < 47.5:
                x = lerp(dcx, dcx + 12, span(t, 46.5, 47.4))
                st.update(pos=(x, fy - self.bob(t)), arms="carry", bundle=True, legs=self.walk(t) if t < 47.4 else "stand",
                          eyes="happy" if t > 47.1 else "normal", layer="street")
                return st
            u = span(t, 47.5, 48.1)
            st.update(pos=(dcx + 12 + 3 * u, fy + 26 * u * u), arms="carry", bundle=True, legs="tuck", eyes="happy",
                      clip=(0, 0, ART_W, LANE_UP - 1), layer="street")
            return st
        return None

    def walk(self, t):
        return ("a", "stand", "b", "stand")[int(t * 9) % 4]

    def bob(self, t):
        return 1 if int(t * 9) % 2 == 0 else 0

    # --------------------------------------------------------------------------------------- doors and lights
    def roof_door(self, t):
        """0 = closed, 1 = fully open."""
        if 38.3 <= t < 39.9:
            return smooth(span(t, 38.3, 38.8)) * (1 - smooth(span(t, 39.7, 39.95)))
        return 0.0

    def lobby_door(self, t):
        if 45.7 <= t < 46.9:
            return smooth(span(t, 45.7, 46.0)) * (1 - smooth(span(t, 46.6, 46.9)))
        return 0.0

    def stair_light(self, t, i):
        t0 = 40.4 + 0.27 * i
        return t0 <= t < t0 + 0.8

    # --------------------------------------------------------------------------------------- extras in the sky
    def far_heli(self, t):
        if 37.0 <= t < 57.0:
            u = span(t, 37.0, 57.0)
            return lerp(ART_W + 20, -30, u), 150 + 10 * math.sin(u * 5)
        return None

    def birds(self, t):
        if not (17.0 <= t < 33.0):
            return []
        u = span(t, 17.0, 33.0)
        out = []
        for i, (dx, dy) in enumerate(((0, 0), (-9, -4), (-9, 5), (-18, -8), (-18, 9), (-27, -11), (-26, 13))):
            x = lerp(-40, ART_W + 40, u) + dx
            y = 204 + dy + 3 * math.sin(u * 9 + i)
            flap = int(t * 7 + i * 0.37) % 2
            out.append((int(x), int(y), flap))
        return out

    def plane(self, t):
        if 12.0 <= t < 54.0:
            u = span(t, 12.0, 54.0)
            return lerp(ART_W + 12, -16, u), 34 + 6 * u
        return None


# ============================================================================================ traffic
class Car:
    def __init__(self, kind, lane, t0, speed, profile=None):
        self.kind, self.lane, self.t0, self.speed, self.profile = kind, lane, t0, speed, profile
        self.right = lane == "up"

    def x(self, t):
        tau = (t - self.t0) % LOOP
        d = self.profile(tau) if self.profile else self.speed * tau
        return -40 + d if self.right else ART_W + 12 - d

    def visible(self, t):
        x = self.x(t)
        return -40 <= x <= ART_W + 12


def taxi_profile(tau):
    """Cruise in from the left, brake to the hotel curb, wait, pull away to the right."""
    stop_x = LOBBY_DOOR[0] + 40             # distance from start (-40) to the stop position
    v = 92.0
    cruise_d = stop_x - 92.0                 # brake over the last 92 px (2 s at average 46 px/s)
    t_cruise = cruise_d / v
    if tau < t_cruise:
        return v * tau
    if tau < t_cruise + 2.0:
        u = tau - t_cruise
        return cruise_d + v * u - 0.5 * (v / 2.0) * u * u
    if tau < t_cruise + 2.0 + 1.7:
        return stop_x
    u = tau - (t_cruise + 3.7)
    return stop_x + 0.5 * 70 * u * u if u < 30 else 1e6


TAXI_T0 = 47.0 - (LOBBY_DOOR[0] + 40 - 92.0) / 92.0 - 2.0


def traffic():
    cars = [
        Car("white", "up", 1.0, 74), Car("teal", "up", 11.5, 70), Car("purple", "up", 22.0, 76),
        Car("van", "up", 30.0, 66),
        Car("taxi", "up", TAXI_T0, 0, taxi_profile),
        Car("sport", "up", 52.5, 84),
        Car("teal", "lo", 4.0, 64), Car("taxi", "lo", 14.5, 68), Car("white", "lo", 24.0, 62),
        Car("van", "lo", 33.5, 60), Car("purple", "lo", 41.0, 66),
        Car("sport", "lo", 52.6, 150), Car("police", "lo", 53.35, 146),
    ]
    return cars


# ============================================================================================ the renderer
class Renderer:
    """frame(t) -> (ART_H, ART_W, 3) uint8. with_story=False draws the city alone (the tests use it to prove that
    nothing from the story is on screen at the loop seam)."""

    def __init__(self, seed=7, with_story=True):
        self.city = City(seed)
        self.story = Story()
        self.cars = traffic()
        self.base = self.city.sky_rgb
        self.with_story = with_story

    def frame(self, t, wrap=True):
        if wrap:
            t = t % LOOP
        c, s = self.city, self.story
        out = self.base.copy()
        story = self.with_story
        c.draw_stars(out, t)
        if story:
            self.draw_plane(out, t)
        c.draw_clouds(out, t)
        if story:
            self.draw_far_heli(out, t)
        c.far.over(out)
        c.draw_blinkers(out, t, "far")
        c.draw_beams(out, t)
        c.bridge.over(out)
        c.draw_blinkers(out, t, "bridge")
        c.draw_bridge_traffic(out, t)
        if story:
            self.draw_birds(out, t)
        c.mid.over(out)
        c.draw_blinkers(out, t, "mid")
        c.draw_toggles(out, t)
        self.draw_tower_story(out, t)
        self.draw_neons_mid(out, t)
        cl = s.claude_state(t) if story else None
        if story:
            self.draw_heli(out, t, cl)
        if cl and cl["layer"] in ("sky", "door"):
            self.draw_claude(out, cl)
        c.near.over(out)
        self.draw_lobby(out, t)
        y0, y1 = c.near_glow_rows
        out[y0:y1] = np.minimum(out[y0:y1].astype(np.float32) + c.near_glow[y0:y1], 255).astype(np.uint8)
        self.draw_neons_near(out, t)
        c.draw_palms(out, t)
        if cl and cl["layer"] == "street":
            self.draw_claude(out, cl)
        self.draw_cars(out, t)
        c.draw_water(out, 0.0)      # the water lies in the still band behind the Dock: frozen at frame 0
        return out

    # ----------------------------------------------------------------------------------------------
    def draw_neons_mid(self, out, t):
        saved = self.city.neons
        self.city.neons = [n for n in saved if n["y"] < 400]
        self.city.draw_neons(out, t)
        self.city.neons = saved

    def draw_neons_near(self, out, t):
        saved = self.city.neons
        self.city.neons = [n for n in saved if n["y"] >= 400]
        self.city.draw_neons(out, t)
        self.city.neons = saved

    def draw_plane(self, out, t):
        p = self.story.plane(t)
        if not p:
            return
        x, y = int(p[0]), int(p[1])
        c = (40, 34, 74)
        fill(out, x, y, x + 9, y + 1, c)
        fill(out, x + 6, y - 1, x + 8, y, c)
        fill(out, x + 3, y + 1, x + 5, y + 2, c)
        if cyc(t, 50) < 0.12:
            put(out, x + 4, y + 1, (255, 255, 255))
            glow(out, x + 4, y + 1, 3, (255, 255, 255), 0.4, 2)
        if cyc(t, 40, 0.5) < 0.5:
            put(out, x, y, (255, 70, 70))

    def draw_far_heli(self, out, t):
        p = self.story.far_heli(t)
        if not p:
            return
        x, y = int(p[0]), int(p[1])
        c = (54, 32, 84)
        fill(out, x + 2, y, x + 8, y + 3, c)
        fill(out, x + 8, y + 1, x + 14, y + 2, c)
        fill(out, x + 3, y + 4, x + 8, y + 5, c)
        w = 6 if int(t * 30) % 2 else 3
        fill(out, x + 5 - w, y - 1, x + 5 + w, y, (90, 60, 110))
        if cyc(t, 60) < 0.3:
            put(out, x + 5, y + 3, (255, 80, 80))
            glow(out, x + 5, y + 3, 3, (255, 80, 80), 0.45, 2)

    def draw_birds(self, out, t):
        for x, y, flap in self.story.birds(t):
            c = (46, 24, 62)
            if flap:
                put(out, x - 1, y - 1, c)
                put(out, x + 1, y - 1, c)
                put(out, x, y, c)
            else:
                put(out, x - 1, y, c)
                put(out, x, y, c)
                put(out, x + 1, y, c)

    def draw_heli(self, out, t, cl):
        hs = self.story.heli_state(t)
        if not hs:
            return
        spr, lo, offs = heli(hs["tilt"])
        x, y = hs["x"], hs["y"] + lo
        blit(out, spr, x, y)
        if cl and cl["layer"] == "heli":
            self.draw_claude(out, cl)
        # main rotor: a faint full disc plus two blades whose apparent length follows the rotation
        hx, hy = x + HUB[0], hs["y"] + HUB[1] + offs[HUB[0]] - 1
        R = 44
        reg_y = hy
        if 0 <= reg_y < out.shape[0]:
            x0, x1 = max(0, hx - R), min(out.shape[1], hx + R + 1)
            if x0 < x1:
                reg = out[reg_y, x0:x1].astype(np.float32)
                out[reg_y, x0:x1] = (reg * 0.55 + np.array((150, 150, 170)) * 0.45).astype(np.uint8)
            for k in (0, math.pi):
                L = R * math.cos(hs["rotor"] + k)
                a, b = sorted((hx, int(hx + L)))
                fill(out, a, reg_y, b + 1, reg_y + 1, (36, 34, 50))
            fill(out, hx - 1, reg_y - 1, hx + 2, reg_y + 1, (36, 34, 50))
        # tail rotor
        tx, ty = x + TAIL_HUB[0], hs["y"] + TAIL_HUB[1] + offs[TAIL_HUB[0]]
        for ang in (hs["rotor"] * 1.7, hs["rotor"] * 1.7 + math.pi / 2):
            ca, sa = math.cos(ang), math.sin(ang)
            draw_pts(out, line_pts(tx - 6 * ca, ty - 6 * sa, tx + 6 * ca, ty + 6 * sa), (54, 52, 70))
        put(out, tx, ty, (90, 90, 110))
        # lights: red beacon on the belly, red on the fin, white strobe on the tail
        if cyc(t, 75) < 0.25:
            px, py = x + 58, hs["y"] + 26 + offs[58]
            put(out, px, py, (255, 80, 80))
            glow(out, px, py, 4, (255, 60, 60), 0.5, 2)
        if cyc(t, 50, 0.5) < 0.1:
            px, py = x + 1, hs["y"] + offs[1]
            put(out, px, py, (255, 255, 255))
            glow(out, px, py, 5, (255, 255, 255), 0.5, 2)

    def draw_claude(self, out, cl):
        spr, ax, ay = claude(cl["arms"], cl["legs"], cl["eyes"], cl["squash"], cl["mouth"], cl["bundle"])
        fx, fy = int(round(cl["pos"][0])), int(round(cl["pos"][1]))
        if cl.get("pile"):
            px, py, k = cl["pile"]
            self.draw_pile(out, px if px is not None else fx, py, k)
        if cl["canopy"]:
            cs, gx, gy = cl["canopy"]
            blit(out, cs, gx, gy)
        for (p0, p1, sag) in cl["lines"]:
            draw_pts(out, curve_pts(p0[0], p0[1], p1[0], p1[1], sag), (30, 26, 40))
        if cl["pilot"]:
            blit(out, Sprite.from_rows(PILOT), cl["pilot"][0], cl["pilot"][1])
        if cl["speed"] > 60:
            n = int(min(14, cl["speed"] / 14))
            for dx in (-6, 0, 7):
                for k in range(n):
                    y = fy - 28 - k - abs(dx) // 3
                    if 0 <= y < out.shape[0] and 0 <= fx + dx < out.shape[1]:
                        out[y, fx + dx] = np.minimum(out[y, fx + dx].astype(int) + 46 - k * 3, 255)
        if cl["tint"] > 0:
            spr = spr.tinted(min(1.0, cl["tint"]) * 0.85, (30, 20, 30))
        blit(out, spr, fx - ax, fy - ay, cl["clip"])

    def draw_pile(self, out, x, y, k):
        spr = Sprite.from_rows(PILE)
        if k >= 0.999:
            blit(out, spr, x, y)
            return
        w = max(9, int(round(spr.w * (0.3 + 0.7 * k))))
        idx = (np.arange(w) * spr.w / w).astype(int)
        sq = Sprite(spr.rgb[:, idx], spr.mask[:, idx])
        blit(out, sq, x - 4, y)

    def draw_tower_story(self, out, t):
        s = self.story
        d = s.roof_door(t) if self.with_story else 0.0
        x0, y0, x1, y1 = DOOR_R
        # interior is always there; the door panel covers it
        for y in range(y0, y1):
            k = (y - y0) / (y1 - y0)
            fill(out, x0, y, x1, y + 1, mix((255, 196, 120), (170, 100, 70), k))
        panel_w = int(round((x1 - x0) * (1 - 0.8 * d)))
        fill(out, x0, y0, x0 + panel_w, y1, (70, 56, 104))
        fill(out, x0, y0, x0 + 1, y1, (96, 80, 130))
        if panel_w > 5:
            fill(out, x0 + panel_w - 3, y0 + 8, x0 + panel_w - 2, y0 + 10, (210, 180, 120))
            fill(out, x0 + 3, y0 + 3, x0 + panel_w - 3, y0 + 6, (150, 130, 110) if d < 0.05 else (255, 220, 150))
        glow(out, (x0 + x1) // 2, y0 - 3, 7, (255, 200, 130), 0.35, 3)
        put(out, (x0 + x1) // 2, y0 - 3, (255, 236, 190))
        if d > 0.05:
            for k in range(4):
                fill(out, x0 - k, y1 + k * 0, x1 + k, y1 + 1, mix((255, 200, 130), (120, 90, 140), 0.5 + k * 0.12))
        # stairwell window and the staircase lights
        if not self.with_story:
            return
        if 40.2 <= t < 41.4:
            fill(out, *self.city.stair_window, (255, 214, 140))
        for i, (wx, wy) in enumerate(self.city.stair_col):
            if s.stair_light(t, i):
                fill(out, wx, wy, wx + 3, wy + 4, (255, 232, 170))
                glow(out, wx + 1, wy + 2, 5, (255, 200, 130), 0.3, 2)

    def draw_lobby(self, out, t):
        d = self.story.lobby_door(t) if self.with_story else 0.0
        if d <= 0:
            return
        x0, y0, x1, y1 = LOBBY_DOOR
        fill(out, x0, y0, x1, y1, (255, 214, 150))
        w = int(round((x1 - x0) * (1 - 0.85 * d)))
        fill(out, x0, y0, x0 + w, y1, (120, 96, 110))
        add_light(out, x0 - 8, y1 - 2, halo(10, 0.35, 3)[10:16, :], (255, 200, 130))

    def draw_cars(self, out, t):
        for car_ in self.cars:
            if not car_.visible(t):
                continue
            x = int(round(car_.x(t)))
            spr = car(car_.kind, car_.right)
            y = (LANE_UP if car_.lane == "up" else LANE_LO) - (CAR_H - 1)
            # headlight beam on the road ahead, a soft cone that fades with distance
            bl = 26
            beam = np.zeros((5, bl), np.float32)
            for i in range(bl):
                k = 1 - i / bl
                lo_, hi_ = 2 - int(i / 9), 3 + int(i / 9)
                beam[max(0, lo_):hi_, i] = 0.34 * k * k
            if not car_.right:
                beam = beam[:, ::-1]
            hx = x + spr.w if car_.right else x - bl
            add_light(out, hx, y + 5, beam, (255, 236, 190))
            blit(out, spr, x, y)
            tx = x + 1 if car_.right else x + spr.w - 2
            glow(out, tx, y + 7, 3, (255, 50, 50), 0.45, 2)
            hl = x + spr.w - 1 if car_.right else x
            glow(out, hl, y + 7, 3, (255, 244, 210), 0.5, 2)
            if car_.kind == "police":
                red = cyc(t, 150) < 0.5
                lx = x + (14 if car_.right else spr.w - 24)
                fill(out, lx, y, lx + 5, y + 1, (255, 60, 60) if red else (90, 30, 40))
                fill(out, lx + 5, y, lx + 10, y + 1, (60, 120, 255) if not red else (30, 40, 90))
                glow(out, lx + (2 if red else 7), y, 12, (255, 60, 60) if red else (60, 120, 255), 0.4, 3)
            if car_.kind == "taxi":
                glow(out, x + spr.w // 2, y, 4, (255, 240, 140), 0.35, 2)
