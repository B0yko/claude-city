#!/usr/bin/env python3
"""Render Claude Parachute.

  render.py video  --out FILE [--seconds N]   the wallpaper video: 864x560, 24 fps, HEVC 4:4:4 (x265, CRF 12)
  render.py poster --out FILE [--scale 4]     frame 0 as PNG: 3456x2234 still wallpaper (4) or the art grid (1)
  render.py still  --out DIR --t T [T...]     stills to look at, scaled by --zoom (default 2)
  render.py gif    --out FILE                 the README preview: the whole loop at 12 fps

The video is the art grid itself and the player scales it up with nearest-neighbour sampling. 4:4:4 keeps one colour
per pixel, so edges stay exact. The 559 art rows are padded to 560 (the last row repeated) so the scale on a 16"
MacBook Pro is exactly 4; the player fills the 2234-row panel and cuts 3 rows at the top and bottom, and the poster
is cut the same way.
"""
import argparse
import os
import subprocess
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scene  # noqa: E402

FPS = 24
PANEL_H = 2234
SRGB = ["-color_primaries", "bt709", "-color_trc", "iec61966-2-1", "-colorspace", "bt709", "-color_range", "tv"]


def frame(r, t):
    f = r.frame(t)
    return np.vstack([f, f[-1:]]) if f.shape[0] % 2 else f


def pipe(cmd, frames):
    p = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                          "-s", f"{scene.ART_W}x{scene.ART_H + scene.ART_H % 2}", *cmd], stdin=subprocess.PIPE)
    for f in frames:
        p.stdin.write(f.tobytes())
    p.stdin.close()
    if p.wait():
        sys.exit("ffmpeg failed")


def cmd_video(a):
    r = scene.Renderer()
    n = int(round((a.seconds or scene.LOOP) * FPS))
    pipe(["-r", str(FPS), "-i", "-",
          "-vf", "scale=out_color_matrix=bt709:out_range=tv:flags=neighbor+full_chroma_int+accurate_rnd,format=yuv444p,"
                 "setparams=color_primaries=bt709:color_trc=iec61966-2-1:colorspace=bt709:range=tv",
          "-c:v", "libx265", "-preset", "slow", "-crf", "12", "-tag:v", "hvc1",
          "-x265-params", f"keyint={FPS * 4}:min-keyint={FPS * 4}:aq-mode=0:no-sao=1:psy-rd=0:log-level=error:"
                          "colorprim=bt709:transfer=iec61966-2-1:colormatrix=bt709:range=limited",
          *SRGB, "-an", "-movflags", "+faststart", a.out],
         (frame(r, i / FPS) for i in range(n)))
    print("wrote", a.out)


def cmd_poster(a):
    img = frame(scene.Renderer(), 0.0)
    img = np.repeat(np.repeat(img, a.scale, 0), a.scale, 1)
    if a.scale == 4:
        top = (img.shape[0] - PANEL_H) // 2
        img = img[top:top + PANEL_H]
    Image.fromarray(img).save(a.out)
    print("wrote", a.out, "%dx%d" % (img.shape[1], img.shape[0]))


def cmd_still(a):
    r = scene.Renderer()
    os.makedirs(a.out, exist_ok=True)
    for t in a.t:
        img = np.repeat(np.repeat(r.frame(t), a.zoom, 0), a.zoom, 1)
        Image.fromarray(img).save(os.path.join(a.out, "t%05.2f.png" % t))
    print("wrote %d stills to %s" % (len(a.t), a.out))


def cmd_gif(a):
    r = scene.Renderer()
    pipe(["-r", "12", "-i", "-", "-filter_complex",
          "split[a][b];[a]palettegen=max_colors=256:stats_mode=full[p];[b][p]paletteuse=dither=none",
          "-loop", "0", a.out],
         (frame(r, i / 12) for i in range(int(scene.LOOP * 12))))
    print("wrote", a.out, "%.1f MB" % (os.path.getsize(a.out) / 1e6))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("video", cmd_video), ("poster", cmd_poster), ("still", cmd_still), ("gif", cmd_gif)):
        s = sub.add_parser(name)
        s.add_argument("--out", required=True)
        s.set_defaults(fn=fn)
        if name == "video":
            s.add_argument("--seconds", type=float)
        if name == "poster":
            s.add_argument("--scale", type=int, choices=[1, 4], default=4)
        if name == "still":
            s.add_argument("--t", type=float, nargs="+", required=True)
            s.add_argument("--zoom", type=int, default=2)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
