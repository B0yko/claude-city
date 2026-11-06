#!/bin/bash
# Claude City installer. https://github.com/B0yko/claude-city
#
#   curl -fsSL https://raw.githubusercontent.com/B0yko/claude-city/main/install.sh | bash
#   ... | bash -s uninstall | pause | resume | status
#
# Installs the release app into ~/Applications (checksum and self-test first), keeps your current wallpaper setting,
# sets a still frame of the scene as the system wallpaper and starts the app at login. An update first removes every
# earlier copy, including ones from the project's earlier name, Claude Parachute. No sudo, no permissions.
# Everything is inside main(), so a cut-off download runs nothing.

main() {
  set -euo pipefail
  VERSION="2.0.0"
  SHA256="e11d70626238d42041f830baa58e06ff0ac75f7cffb71e5f5d85f44c2d269a0e"
  URL="https://github.com/B0yko/claude-city/releases/download/v$VERSION/Claude-City-$VERSION.zip"
  LABEL="local.claudecity.wallpaper"
  APP="$HOME/Applications/Claude City.app"
  BIN="$APP/Contents/MacOS/ClaudeCity"
  DATA="$HOME/Library/Application Support/Claude City"
  LOG="$HOME/Library/Logs/ClaudeCity.log"
  PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
  OLD_LABEL="local.claudechute.wallpaper"                        # Claude Parachute 1.x
  OLD_APP="$HOME/Applications/Claude Parachute.app"
  OLD_DATA="$HOME/Library/Application Support/Claude Parachute"
  STORE="$HOME/Library/Application Support/com.apple.wallpaper/Store/Index.plist"
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

# Stop and delete every installed copy, this one and Claude Parachute's, so nothing is left behind. Only the saved
# wallpaper setting survives (moved over from Claude Parachute if that is where it is).
remove_installed() {
  local label
  for label in "$LABEL" "$OLD_LABEL"; do
    launchctl bootout "$DOMAIN/$label" 2>/dev/null || true
    rm -f -- "$HOME/Library/LaunchAgents/$label.plist"
    defaults delete "$label" >/dev/null 2>&1 || true
  done
  rm -rf -- "${APP:?}" "${OLD_APP:?}"
  rm -f -- "$LOG" "$HOME/Library/Logs/ClaudeChute.log"
  mkdir -p "$DATA"
  if [ -f "$OLD_DATA/wallpaper-Index.plist" ] && [ ! -f "$DATA/wallpaper-Index.plist" ]; then
    mv "$OLD_DATA/wallpaper-Index.plist" "$DATA/wallpaper-Index.plist"
  fi
  rm -rf -- "${OLD_DATA:?}"
}

install() {
  [ "$(id -u)" -ne 0 ] || die "run it without sudo"
  [ "$(sw_vers -productVersion | cut -d. -f1)" -ge 13 ] || die "needs macOS 13 or newer"
  local src="$1"
  TMP="$(mktemp -d)"
  trap 'rm -rf -- "$TMP"' EXIT
  if [ -z "$src" ]; then
    echo "Downloading Claude City $VERSION"
    curl -fL# --retry 3 -o "$TMP/app.zip" "$URL" || die "download failed"
    [ "$(shasum -a 256 "$TMP/app.zip" | cut -d' ' -f1)" = "$SHA256" ] || die "checksum mismatch"
    src="$TMP/app.zip"
  fi
  case "$src" in
    *.zip) ditto -x -k "$src" "$TMP" ;;
    *) ditto "$src" "$TMP/Claude City.app" ;;
  esac
  "$TMP/Claude City.app/Contents/MacOS/ClaudeCity" --self-test > "$TMP/self-test.txt" 2>&1 \
    || { cat "$TMP/self-test.txt" >&2; die "the app's self-test failed on this Mac"; }

  remove_installed
  mkdir -p "$HOME/Applications" "$(dirname "$PLIST")" "$(dirname "$LOG")"
  ditto "$TMP/Claude City.app" "$APP"

  # remember your wallpaper once (updates keep the original), then show a still frame of the scene. The poster gets
  # a new file name each time: macOS ignores a "change" to the path it already shows, and deleting the file it shows
  # first would drop it back to the default picture. So: new file, switch to it, then delete the older posters.
  [ -f "$DATA/wallpaper-Index.plist" ] || cp "$STORE" "$DATA/wallpaper-Index.plist" 2>/dev/null || true
  local poster old
  poster="$DATA/poster-$(date +%Y%m%d%H%M%S).png"
  cp "$APP/Contents/Resources/wallpaper.png" "$poster"
  "$BIN" --set-wallpaper "$poster" || true
  for old in "$DATA"/poster*.png "$HOME/Pictures/Claude Parachute.png"; do
    [ "$old" = "$poster" ] || rm -f -- "$old"
  done

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
  echo "Installed Claude City $VERSION. It animates while the desktop is in front."
}

uninstall() {
  remove_installed
  if [ -f "$DATA/wallpaper-Index.plist" ]; then
    cp "$DATA/wallpaper-Index.plist" "$STORE"
    killall WallpaperAgent 2>/dev/null || true
  fi
  rm -rf -- "${DATA:?}"
  echo "Uninstalled. Your previous wallpaper is back."
}

status() {
  local pid
  pid="$(launchctl print "$DOMAIN/$LABEL" 2>/dev/null | awk -F' = ' $'/^\tpid = / {print $2}')" || pid=""
  if [ -z "$pid" ]; then
    if [ -f "$HOME/Library/LaunchAgents/$OLD_LABEL.plist" ]; then echo "Claude Parachute (the earlier version) is installed: run install to update"
    elif [ -f "$PLIST" ]; then echo "installed, not running"
    else echo "not installed"; fi
    return 0
  fi
  ps -o %cpu=,rss= -p "$pid" | awk '{printf "running: cpu %s%%, memory %.0f MB\n", $1, $2/1024}'
  grep -E ' (play|pause)' "$LOG" 2>/dev/null | tail -n 1 | cut -d' ' -f2- || true
}

main "$@"
