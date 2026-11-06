#!/bin/bash
# Claude Parachute installer. https://github.com/B0yko/claude-parachute-wallpaper
#
#   curl -fsSL https://raw.githubusercontent.com/B0yko/claude-parachute-wallpaper/main/install.sh | bash
#   ... | bash -s uninstall | pause | resume | status
#
# Installs the release app into ~/Applications (checksum and self-test first), keeps your current wallpaper setting,
# sets a still frame of the scene as the system wallpaper and starts the app at login. No sudo, no permissions.
# Everything is inside main(), so a cut-off download runs nothing.

main() {
  set -euo pipefail
  VERSION="1.1.0"
  SHA256="0d5c147f9b36be1f8473b3d3ca1510173c94a052dadc129fabda6f7f82afcbd6"
  URL="https://github.com/B0yko/claude-parachute-wallpaper/releases/download/v$VERSION/Claude-Parachute-$VERSION.zip"
  LABEL="local.claudechute.wallpaper"
  APP="$HOME/Applications/Claude Parachute.app"
  BIN="$APP/Contents/MacOS/ClaudeChute"
  PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
  DATA="$HOME/Library/Application Support/Claude Parachute"
  STORE="$HOME/Library/Application Support/com.apple.wallpaper/Store/Index.plist"
  LOG="$HOME/Library/Logs/ClaudeChute.log"
  DOMAIN="gui/$(id -u)"

  case "${1:-install}" in
    install) install "${2:-}" ;;         # an optional local .app or .zip instead of the release (build.sh uses it)
    uninstall) uninstall ;;
    pause|resume) [ -x "$BIN" ] || die "not installed"; "$BIN" --send "$1"; echo "${1}d" ;;
    status) status ;;
    *) die "usage: install.sh [install | uninstall | pause | resume | status]" ;;
  esac
}

die() { echo "error: $*" >&2; exit 1; }

install() {
  [ "$(id -u)" -ne 0 ] || die "run it without sudo"
  [ "$(sw_vers -productVersion | cut -d. -f1)" -ge 13 ] || die "needs macOS 13 or newer"
  local tmp src="$1"
  tmp="$(mktemp -d)"
  if [ -z "$src" ]; then
    echo "Downloading Claude Parachute $VERSION"
    curl -fL# --retry 3 -o "$tmp/app.zip" "$URL" || die "download failed"
    [ "$(shasum -a 256 "$tmp/app.zip" | cut -d' ' -f1)" = "$SHA256" ] || die "checksum mismatch"
    src="$tmp/app.zip"
  fi
  case "$src" in
    *.zip) ditto -x -k "$src" "$tmp" ;;
    *) ditto "$src" "$tmp/Claude Parachute.app" ;;
  esac
  "$tmp/Claude Parachute.app/Contents/MacOS/ClaudeChute" --self-test > "$tmp/self-test.txt" 2>&1 \
    || { cat "$tmp/self-test.txt" >&2; die "the app's self-test failed on this Mac"; }

  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
  mkdir -p "$HOME/Applications" "$DATA" "$(dirname "$PLIST")" "$(dirname "$LOG")"
  rm -rf -- "${APP:?}"
  ditto "$tmp/Claude Parachute.app" "$APP"
  rm -rf -- "${tmp:?}"

  # remember your wallpaper once (an update keeps the original), then show a still frame of the scene
  [ -f "$DATA/wallpaper-Index.plist" ] || cp "$STORE" "$DATA/wallpaper-Index.plist" 2>/dev/null || true
  cp "$APP/Contents/Resources/wallpaper.png" "$DATA/poster.png"
  "$BIN" --set-wallpaper "$DATA/poster.png" || true

  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key><string>$LABEL</string>
	<key>ProgramArguments</key><array><string>$BIN</string></array>
	<key>RunAtLoad</key><true/>
	<key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
	<key>LimitLoadToSessionType</key><string>Aqua</string>
	<key>ProcessType</key><string>Interactive</string>
	<key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
EOF
  launchctl enable "$DOMAIN/$LABEL"
  launchctl bootstrap "$DOMAIN" "$PLIST"
  echo "Installed Claude Parachute $VERSION. It animates while the desktop is in front."
}

uninstall() {
  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
  rm -f -- "$PLIST" "$LOG"
  rm -rf -- "${APP:?}"
  if [ -f "$DATA/wallpaper-Index.plist" ]; then
    cp "$DATA/wallpaper-Index.plist" "$STORE"
    killall WallpaperAgent 2>/dev/null || true
  fi
  rm -rf -- "${DATA:?}"
  echo "Uninstalled. Your previous wallpaper is back."
}

status() {
  local pid
  pid="$(launchctl print "$DOMAIN/$LABEL" 2>/dev/null | awk -F' = ' $'/^\tpid = / {print $2}')"
  if [ -z "$pid" ]; then
    [ -f "$PLIST" ] && echo "installed, not running" || echo "not installed"
    return 0
  fi
  ps -o %cpu=,rss= -p "$pid" | awk '{printf "running: cpu %s%%, memory %.0f MB\n", $1, $2/1024}'
  grep -E ' (play|pause)' "$LOG" 2>/dev/null | tail -n 1 | cut -d' ' -f2- || true
}

main "$@"
