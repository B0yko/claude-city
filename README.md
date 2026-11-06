# Claude City

Animated pixel-art wallpaper for macOS: Claude jumps out of a helicopter over a neon city at sunset, lands on a
rooftop and leaves in a taxi.

![Claude City](preview.gif)

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/B0yko/claude-city/main/install.sh | bash
```

Run it again to update: the new version replaces the old one and nothing is left behind.

Uninstall (your previous wallpaper comes back):

```bash
curl -fsSL https://raw.githubusercontent.com/B0yko/claude-city/main/install.sh | bash -s uninstall
```

`pause`, `resume` and `status` work the same way.

It animates only while the desktop is in front and stands still, using no CPU, while you work in an app, the screen
is locked or the Mac sleeps. macOS 13+, Apple Silicon or Intel, no permissions needed.

## Build

Needs Xcode Command Line Tools, Python 3 and `brew install ffmpeg`.

```bash
./build.sh && ./build.sh install
```

MIT license. A fan project, not affiliated with Anthropic.
