#!/usr/bin/env python3
"""Render the Claude Parachute wallpaper.

  render.py still  --t 0 9.5 20 --out DIR [--zoom 2]     PNG stills (art grid scaled by --zoom)
  render.py sheet  --out FILE [--cols 4] [--every 4]       contact sheet of the loop for a quick look
  render.py poster --out FILE [--scale 4]                  PNG of frame 0 (scale 4: the static wallpaper, 1: app)
  render.py video  --out FILE [--fps 24] [--scale 1] [--chroma 444] [--bits 8] [--seconds N] [--crf 12]

The wallpaper video is the art grid itself: 864x560 (the last art row repeated so the height is even), HEVC 4:4:4,
24 fps. 4:4:4 keeps a colour per pixel, so edges stay exact, and Apple's media engine decodes it in hardware; the
player scales it x4 to the panel with nearest-neighbour sampling. Decoding 1/16 of the panel's pixels at 24 fps is
what makes the wallpaper almost free to run (measured: player ~1% CPU, 45 MB). --scale 4 --fps 60 --chroma 420
--bits 10 gives the earlier full-resolution master instead.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scene  # noqa: E402

PANEL = (3456, 2234)  # 16" MacBook Pro native resolution


def upscale(img, k):
    return np.repeat(np.repeat(img, k, axis=0), k, axis=1) if k > 1 else img


def cmd_still(a):
    r = scene.Renderer()
    os.makedirs(a.out, exist_ok=True)
    for t in a.t:
        Image.fromarray(upscale(r.frame(t), a.zoom)).save(os.path.join(a.out, "t%05.2f.png" % t))
    print("wrote %d stills to %s" % (len(a.t), a.out))


def cmd_sheet(a):
    r = scene.Renderer()
    times = [i * a.every for i in range(int(scene.LOOP / a.every))]
    frames = [r.frame(t) for t in times]
    h, w = frames[0].shape[:2]
    rows = (len(frames) + a.cols - 1) // a.cols
    sheet = np.zeros((rows * (h + 4), a.cols * (w + 4), 3), np.uint8)
    for i, f in enumerate(frames):
        y, x = (i // a.cols) * (h + 4), (i % a.cols) * (w + 4)
        sheet[y:y + h, x:x + w] = f
    Image.fromarray(sheet).save(a.out)
    print("wrote", a.out, "times", times)


def cmd_poster(a):
    """Same geometry as the video: the art grid padded to 560 rows; at scale 4 the middle 2234 rows, which is what
    the player shows when it fills the panel with the 864x560 video (3 rows cut at the top and at the bottom)."""
    r = scene.Renderer()
    f = r.frame(a.t)
    if f.shape[0] % 2:
        f = np.vstack([f, f[-1:]])
    img = upscale(f, a.scale)
    if a.scale == 4:
        top = (img.shape[0] - PANEL[1]) // 2
        img = img[top:top + PANEL[1], :PANEL[0]]
    Image.fromarray(img).save(a.out)
    print("wrote", a.out, img.shape[1], "x", img.shape[0])


def cmd_video(a):
    r = scene.Renderer()
    fps = a.fps
    n = int(round((a.seconds or scene.LOOP) * fps))
    h, w = scene.ART_H, scene.ART_W
    hin = h + h % 2          # even rows: repeat the last art row (water under the Dock); the scale stays an integer
    ow, oh = w * a.scale, hin * a.scale
    cw, ch = PANEL if a.scale == 4 else (ow, oh)
    pix = {(8, 420): "yuv420p", (10, 420): "yuv420p10le", (8, 444): "yuv444p", (10, 444): "yuv444p10le"}[(a.bits, a.chroma)]
    vf = (f"scale={ow}:{oh}:flags=neighbor,crop={cw}:{ch}:0:0,"
          f"scale=out_color_matrix=bt709:out_range=tv:flags=neighbor+full_chroma_int+accurate_rnd,format={pix},"
          f"setparams=color_primaries=bt709:color_trc=iec61966-2-1:colorspace=bt709:range=tv")
    if a.codec == "x265":
        enc = ["-c:v", "libx265", "-preset", a.preset, "-crf", str(a.crf), "-tag:v", "hvc1",
               "-x265-params", f"keyint={fps * 4}:min-keyint={fps * 4}:aq-mode=0:no-sao=1:psy-rd=0:log-level=error:"
               f"colorprim=bt709:transfer=iec61966-2-1:colormatrix=bt709:range=limited"]
    else:
        enc = ["-c:v", "hevc_videotoolbox", "-q:v", str(a.q), "-tag:v", "hvc1",
               "-profile:v", "main" if a.bits == 8 else "main10"]
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{hin}",
           "-r", str(fps), "-i", "-", "-vf", vf, *enc,
           "-color_primaries", "bt709", "-color_trc", "iec61966-2-1", "-colorspace", "bt709", "-color_range", "tv",
           "-an", "-movflags", "+faststart", a.out]
    print(" ".join(cmd), flush=True)
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    t0 = time.time()
    for i in range(n):
        f = r.frame(i / fps)
        if hin > h:
            f = np.vstack([f, f[-1:]])
        p.stdin.write(f.tobytes())
        if i % (fps * 5) == 0:
            el = time.time() - t0
            print("frame %d/%d  %.0fs elapsed" % (i, n, el), flush=True)
    p.stdin.close()
    rc = p.wait()
    print("done in %.0fs, exit %d" % (time.time() - t0, rc))
    sys.exit(rc)


def cmd_gif(a):
    """Animated preview for the README: the art grid, a 256-colour palette built from the whole clip, no dithering
    (pixel art keeps its flat colours), looping forever."""
    r = scene.Renderer()
    h, w = scene.ART_H, scene.ART_W
    n = int(round((a.end - a.start) * a.fps))
    vf = (f"scale={w * a.zoom}:{h * a.zoom}:flags=neighbor,split[a][b];[a]palettegen=max_colors=256:stats_mode=full[p];"
          f"[b][p]paletteuse=dither=none")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
           "-r", str(a.fps), "-i", "-", "-filter_complex", vf, "-loop", "0", a.out]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for i in range(n):
        p.stdin.write(r.frame(a.start + i / a.fps).tobytes())
    p.stdin.close()
    rc = p.wait()
    print("wrote", a.out, "%.1f MB" % (os.path.getsize(a.out) / 1e6) if rc == 0 else "", "exit", rc)
    sys.exit(rc)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("still")
    s.add_argument("--t", type=float, nargs="+", default=[0.0])
    s.add_argument("--out", required=True)
    s.add_argument("--zoom", type=int, default=2)
    s.set_defaults(fn=cmd_still)
    s = sub.add_parser("sheet")
    s.add_argument("--out", required=True)
    s.add_argument("--cols", type=int, default=4)
    s.add_argument("--every", type=float, default=4.0)
    s.set_defaults(fn=cmd_sheet)
    s = sub.add_parser("poster")
    s.add_argument("--out", required=True)
    s.add_argument("--t", type=float, default=0.0)
    s.add_argument("--scale", type=int, default=4)
    s.set_defaults(fn=cmd_poster)
    s = sub.add_parser("video")
    s.add_argument("--out", required=True)
    s.add_argument("--fps", type=int, default=24)
    s.add_argument("--scale", type=int, default=1)
    s.add_argument("--bits", type=int, choices=[8, 10], default=8)
    s.add_argument("--chroma", type=int, choices=[420, 444], default=444)
    s.add_argument("--seconds", type=float, default=None)
    s.add_argument("--codec", choices=["x265", "vt"], default="x265")
    s.add_argument("--preset", default="slow")
    s.add_argument("--crf", type=int, default=12)
    s.add_argument("--q", type=int, default=72)
    s.set_defaults(fn=cmd_video)
    s = sub.add_parser("gif")
    s.add_argument("--out", required=True)
    s.add_argument("--fps", type=int, default=12)
    s.add_argument("--start", type=float, default=0.0)
    s.add_argument("--end", type=float, default=scene.LOOP)
    s.add_argument("--zoom", type=int, default=1)
    s.set_defaults(fn=cmd_gif)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
