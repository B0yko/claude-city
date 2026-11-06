# Claude Parachute

An animated pixel-art wallpaper for macOS. A helicopter flies over a city at sunset, Claude jumps out, opens a
parachute, lands on a skyscraper roof under a neon CLAUDE sign, packs the canopy, takes the stairs down and leaves in
a taxi. Then the helicopter comes around for the next drop.

![Claude Parachute](docs/preview.gif)

*Preview at 12 fps; the wallpaper runs at 24 fps, pixel for pixel on a 16" MacBook Pro.*

[Русская версия](README.ru.md)

## Install

Paste into Terminal (no sudo, no Xcode, nothing else to install):

```bash
curl -fsSL https://raw.githubusercontent.com/B0yko/claude-parachute-wallpaper/main/install.sh | bash
```

Run the same line again to update. Remove it and get your old wallpaper back:

```bash
curl -fsSL https://raw.githubusercontent.com/B0yko/claude-parachute-wallpaper/main/install.sh | bash -s -- uninstall
```

The installer downloads the release from this repo, checks its SHA-256, runs the app's self-test, puts
`Claude Parachute.app` in `~/Applications`, remembers your current wallpaper, sets a still frame of the scene as the
system wallpaper and adds a LaunchAgent so it starts at login. macOS may show a "Background Items Added" notice: that
is the LaunchAgent. Read [install.sh](install.sh) before you run it; it is short.

## Controls

Every command goes through the same one-liner, `... | bash -s -- COMMAND`:

| Command | What it does |
|---|---|
| `status` | running or not, CPU, memory, playing or paused |
| `pause`, `resume` | stop / continue the animation |
| `stop`, `start` | turn it off (stays off after a reboot) / on |
| `mode visible` | animate whenever the wallpaper can be seen |
| `mode desktop` | animate only while the desktop is in front (default) |
| `battery pause` | stand still when running on battery |
| `battery play` | keep playing on battery (default) |
| `check` | download and verify the release, run its self-test, change nothing |
| `uninstall` | remove everything, restore your previous wallpaper |

Offline, the same script is inside the app:
`~/Applications/Claude\ Parachute.app/Contents/Resources/claude-parachute.sh status`.

## Built to cost almost nothing

A live wallpaper should not drain the battery. This one:

- **Plays only when you look.** By default it animates while the desktop is in front (you clicked the desktop or
  hid your apps) and part of it is visible. While you work in an app it is a still picture, like Apple's own aerial
  wallpapers on the desktop. Window occlusion alone is not enough: macOS treats the wallpaper as visible behind any
  window that is not marked opaque (translucent sidebars, many Electron apps), so the window server would keep
  compositing an animation nobody sees.
- **Pauses** while the displays sleep, the screen saver runs, the screen is locked, another user is active, Low
  Power Mode is on, the Mac runs hot, and on battery if you ask it to. Paused, it uses 0% CPU. It never holds a power
  assertion, so your Mac and displays sleep as usual.
- **Decodes a tiny video.** The video is the pixel art itself, 864x560 at 24 fps (HEVC 4:4:4, one colour per pixel,
  so edges stay exact), decoded by the media engine and scaled up by the compositor with nearest-neighbour sampling.
  That is 1/16 of the pixels of the panel.
- **Keeps the menu bar and Dock strips still.** Behind the translucent menu bar and Dock it shows a still frame, so
  macOS does not re-blur the wallpaper 24 times a second. Nothing in the scene moves there (a test checks every
  frame); with a bigger Dock or a differently shaped screen it plays video there too.

Measured on an M5 Pro MacBook Pro, desktop visible, animation playing:

| | App CPU | GPU | WindowServer CPU |
|---|---|---|---|
| no live wallpaper | 0 | 0% | 0.2% |
| first version (3456x2234, 60 fps, full-screen video) | 4.5% | 14% | 20% |
| this release | 1.3% | ~0% | 6% |
| any app in front (default mode) | 0% | 0% | unchanged |

## Requirements

- macOS 13 Ventura or newer, Apple Silicon or Intel (universal app; Intel Macs decode the video in software, which
  at this size is cheap).
- Any display. On a 16" MacBook Pro (3456x2234) every art pixel is exactly 4x4 screen pixels; other screens get the
  same crisp nearest-neighbour scaling with slightly uneven pixel sizes.
- No permissions: no screen recording, no accessibility, no network after install, no analytics.

## How it works

| Part | |
|---|---|
| [render/scene.py](render/scene.py) | The whole scene as code: sky, sun, bridge, three layers of buildings, neon, palms, traffic, the story. Every frame is a pure function of time; everything periodic makes a whole number of cycles per 60 s loop and the story starts and ends off screen, so the loop has no seam. |
| [render/render.py](render/render.py) | Renders the video (x265, HEVC 4:4:4, sRGB-tagged so colours match), posters and the preview GIF. |
| [render/test_scene.py](render/test_scene.py) | 17 tests: seamless loop, story off screen at the seam, Claude never teleports and lands exactly on the roof, cars never overlap, the taxi waits, the menu bar and Dock bands never change. |
| [player/main.swift](player/main.swift) | The app (one file, AppKit + AVFoundation). Tile windows just below the desktop icons on every Space, one hardware-decoded player for all of them, the play/pause rules. `--self-test` checks the rules, the tile layout and the video. |
| [install.sh](install.sh) | Installer and controller. |
| [chute](chute) | Developer tool: build, test, preview, release. |

## Build from source

Needs the Xcode Command Line Tools, Python 3 and ffmpeg with libx265 (`brew install ffmpeg`).

```bash
git clone https://github.com/B0yko/claude-parachute-wallpaper && cd claude-parachute-wallpaper
./chute test        # scene tests (creates .venv with numpy and pillow)
./chute build       # renders the video (~20 s) and builds the universal app into build/
./chute install     # installs that build, same steps as the one-liner
```

`./chute preview 9.5 30` renders stills to look at; the story lives in `Story` in `render/scene.py`.

## License

MIT, see [LICENSE](LICENSE). A fan project, not affiliated with or endorsed by Anthropic; Claude and its mascot
belong to Anthropic.
