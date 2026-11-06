#!/bin/bash
# ./build.sh                build the app into build/ (renders the video first if the scene changed)
# ./build.sh test           scene tests and the app's self-test
# ./build.sh install        install that build
# ./build.sh release X.Y.Z  build, zip, pin the checksum in install.sh, tag, push, publish the GitHub release
set -euo pipefail
cd "$(dirname "$0")"
PY=.venv/bin/python
APP="build/Claude Parachute.app"

venv() { [ -x "$PY" ] || { python3 -m venv .venv && "$PY" -m pip install -q numpy pillow; }; }

build() {
  venv
  mkdir -p build
  if [ ! -f build/claude-parachute.mp4 ] || [ src/scene.py -nt build/claude-parachute.mp4 ] \
     || [ src/render.py -nt build/claude-parachute.mp4 ]; then
    "$PY" src/render.py video --out build/claude-parachute.mp4
    "$PY" src/render.py poster --out build/wallpaper.png
    "$PY" src/render.py poster --scale 1 --out build/poster.png
  fi
  rm -rf "$APP"
  mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
  for arch in arm64 x86_64; do
    swiftc -O -swift-version 5 -target "$arch-apple-macos13.0" src/main.swift -o "build/ClaudeChute-$arch"
  done
  lipo -create build/ClaudeChute-arm64 build/ClaudeChute-x86_64 -output "$APP/Contents/MacOS/ClaudeChute"
  rm build/ClaudeChute-arm64 build/ClaudeChute-x86_64
  cp src/Info.plist "$APP/Contents/"
  cp build/claude-parachute.mp4 build/wallpaper.png build/poster.png "$APP/Contents/Resources/"
  codesign --force --sign - "$APP"
  "$APP/Contents/MacOS/ClaudeChute" --self-test | tail -1
}

release() {
  local v="${1:?usage: ./build.sh release X.Y.Z}" zip sha token
  [ -z "$(git status --porcelain)" ] || { echo "commit your changes first" >&2; exit 1; }
  "$PY" -m unittest discover -s src -q
  sed -i '' "s/<string>[0-9.]*<\/string><!-- version -->/<string>$v<\/string><!-- version -->/" src/Info.plist
  build
  zip="build/Claude-Parachute-$v.zip"
  rm -f "$zip"
  ditto -c -k --keepParent "$APP" "$zip"
  sha="$(shasum -a 256 "$zip" | cut -d' ' -f1)"
  sed -i '' -e "s/^  VERSION=\".*\"/  VERSION=\"$v\"/" -e "s/^  SHA256=\".*\"/  SHA256=\"$sha\"/" install.sh
  git commit -qam "Release $v"
  git tag -a "v$v" -m "Claude Parachute $v"
  # the repo is on the personal account B0yko: its token serves gh and git without switching the active account
  token="$(gh auth token -u B0yko)"
  GH_TOKEN="$token" git push -q origin main "v$v"
  GH_TOKEN="$token" gh release create "v$v" "$zip" --repo B0yko/claude-parachute-wallpaper \
    --title "Claude Parachute $v" --notes "Install: \`curl -fsSL https://raw.githubusercontent.com/B0yko/claude-parachute-wallpaper/main/install.sh | bash\`"
}

case "${1:-build}" in
  build) build ;;
  test) venv; "$PY" -m unittest discover -s src -q; [ ! -x "$APP/Contents/MacOS/ClaudeChute" ] || "$APP/Contents/MacOS/ClaudeChute" --self-test ;;
  install) [ -d "$APP" ] || build; /bin/bash install.sh install "$PWD/$APP" ;;
  release) release "${2:-}" ;;
  *) sed -n '2,5p' "$0"; exit 1 ;;
esac
