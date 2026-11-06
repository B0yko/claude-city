#!/bin/bash
# Claude Parachute: an animated pixel-art wallpaper for macOS.
# https://github.com/B0yko/claude-parachute-wallpaper
#
# Install or update (one line, no sudo):
#   curl -fsSL https://raw.githubusercontent.com/B0yko/claude-parachute-wallpaper/main/install.sh | bash
#
# Everything else goes through the same script:
#   ... | bash -s -- uninstall            remove it and restore your previous wallpaper
#   ... | bash -s -- status | pause | resume | stop | start | restart
#   ... | bash -s -- mode desktop|visible animate only with the desktop in front (default) / whenever visible
#   ... | bash -s -- battery pause|play   stand still on battery / keep playing (default)
#   ... | bash -s -- check                download and verify the release, run its self-test, change nothing
#
# What install does: downloads the release zip from GitHub, checks its SHA-256, runs the app's self-test, puts
# "Claude Parachute.app" in ~/Applications, keeps a copy of your current wallpaper setting, sets a still frame of
# the scene as the system wallpaper, and adds a LaunchAgent so it starts at login. Nothing needs admin rights,
# no permission prompts, no network use after install.
#
# The whole script is one function called on the last line, so a cut-off download runs nothing.

main() {
  set -euo pipefail

  VERSION="1.0.0"
  SHA256="d95371632a738780a083f95c98f9dd59485c19d504942fa74d77c86c23bb68c5"
  REPO="B0yko/claude-parachute-wallpaper"
  ZIP_URL="https://github.com/$REPO/releases/download/v$VERSION/Claude-Parachute-$VERSION.zip"

  LABEL="local.claudechute.wallpaper"
  APP_NAME="Claude Parachute.app"
  APP_DIR="$HOME/Applications"
  APP="$APP_DIR/$APP_NAME"
  BIN="$APP/Contents/MacOS/ClaudeChute"
  PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
  SUPPORT="$HOME/Library/Application Support/Claude Parachute"
  BACKUP="$SUPPORT/wallpaper-Index.plist"
  POSTER="$SUPPORT/poster.png"
  LOG="$HOME/Library/Logs/ClaudeChute.log"
  WP_STORE="$HOME/Library/Application Support/com.apple.wallpaper/Store/Index.plist"
  DOMAIN="gui/$(id -u)"

  local from="" cmd="install" args=()
  while [ $# -gt 0 ]; do
    case "$1" in
      --from) from="${2:-}"; shift 2 ;;
      -h|--help|help) cmd="help"; shift ;;
      *) if [ "$cmd" = "install" ] && [ ${#args[@]} -eq 0 ] && is_command "$1"; then cmd="$1"; else args+=("$1"); fi; shift ;;
    esac
  done

  case "$cmd" in
    install) do_install "$from" ;;
    uninstall) do_uninstall ;;
    check) do_check "$from" ;;
    status) do_status ;;
    start) need_installed; launchctl enable "$DOMAIN/$LABEL"; if loaded; then launchctl kickstart "$DOMAIN/$LABEL"; else launchctl bootstrap "$DOMAIN" "$PLIST"; fi; ok "started" ;;
    stop) need_installed; if loaded; then launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true; fi; launchctl disable "$DOMAIN/$LABEL"; ok "stopped (stays off after a reboot; 'start' turns it back on)" ;;
    restart) need_installed; launchctl kickstart -k "$DOMAIN/$LABEL"; ok "restarted" ;;
    pause) need_installed; "$BIN" --send pause; ok "paused" ;;
    resume) need_installed; "$BIN" --send resume; ok "resumed" ;;
    mode) need_installed
          case "${args[0]:-}" in desktop|visible) set_pref PlayWhen -string "${args[0]}" ;; *) die "usage: mode desktop|visible" ;; esac ;;
    battery) need_installed
          case "${args[0]:-}" in pause) set_pref PauseOnBattery -bool true ;; play) set_pref PauseOnBattery -bool false ;; *) die "usage: battery pause|play" ;; esac ;;
    help) usage ;;
  esac
}

is_command() {
  case "$1" in install|uninstall|check|status|start|stop|restart|pause|resume|mode|battery) return 0 ;; esac
  return 1
}

if [ -t 1 ]; then B=$'\033[1m'; G=$'\033[32m'; R=$'\033[31m'; D=$'\033[2m'; N=$'\033[0m'; else B=""; G=""; R=""; D=""; N=""; fi
say() { printf '%s\n' "${B}$*${N}"; }
ok() { printf '%s\n' "${G}✓${N} $*"; }
die() { printf '%s\n' "${R}✗ $*${N}" >&2; exit 1; }
loaded() { launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; }
need_installed() { [ -x "$BIN" ] && [ -f "$PLIST" ] || die "Claude Parachute is not installed"; }

usage() {
  cat <<EOF
Claude Parachute $VERSION: an animated pixel-art wallpaper for macOS (https://github.com/$REPO)

  curl -fsSL https://raw.githubusercontent.com/$REPO/main/install.sh | bash -s -- COMMAND

  install            install or update (the default)
  uninstall          remove it and restore your previous wallpaper
  status             running? CPU, memory, playing or paused
  pause | resume     stop / continue the animation
  stop | start       turn it off (also after a reboot) / on
  restart
  mode desktop       animate only while the desktop is in front (default)
  mode visible       animate whenever the wallpaper can be seen
  battery pause      stand still on battery
  battery play       keep playing on battery (default)
  check              download and verify the release, run its self-test, change nothing
EOF
}

set_pref() {  # set_pref KEY TYPE VALUE
  local key="$1" type="$2" value="$3"
  defaults write "$LABEL" "$key" "$type" "$value"
  if loaded; then launchctl kickstart -k "$DOMAIN/$LABEL"; fi
  ok "$key = $value"
}

preflight() {
  [ "$(uname -s)" = "Darwin" ] || die "this wallpaper is for macOS"
  [ "$(id -u)" -ne 0 ] || die "run it as yourself, not with sudo"
  local major
  major="$(sw_vers -productVersion | cut -d. -f1)"
  [ "$major" -ge 13 ] || die "needs macOS 13 Ventura or newer (this Mac has $(sw_vers -productVersion))"
}

# fetch DIR: put a verified "Claude Parachute.app" into DIR (from --from, or the GitHub release)
fetch() {
  local dir="$1" from="$2"
  if [ -n "$from" ]; then
    case "$from" in
      *.zip) ditto -x -k "$from" "$dir" ;;
      *.app) ditto "$from" "$dir/$APP_NAME" ;;
      *) die "--from wants a .app or a .zip" ;;
    esac
  else
    [ "$SHA256" != "__SHA256__" ] || die "this copy of the script has no release pinned; use the one from GitHub"
    say "Downloading Claude Parachute $VERSION"
    curl -fL --retry 3 --progress-bar -o "$dir/release.zip" "$ZIP_URL" || die "download failed: $ZIP_URL"
    local got
    got="$(shasum -a 256 "$dir/release.zip" | cut -d' ' -f1)"
    [ "$got" = "$SHA256" ] || die "checksum mismatch: expected $SHA256, got $got"
    ok "checksum verified"
    ditto -x -k "$dir/release.zip" "$dir"
  fi
  [ -x "$dir/$APP_NAME/Contents/MacOS/ClaudeChute" ] || die "the archive has no $APP_NAME"
  if ! "$dir/$APP_NAME/Contents/MacOS/ClaudeChute" --self-test > "$dir/self-test.txt" 2>&1; then
    cat "$dir/self-test.txt" >&2
    die "the app's self-test failed on this Mac"
  fi
  ok "self-test passed ($(grep -c '^ok' "$dir/self-test.txt") checks)"
}

do_check() {
  preflight
  local tmp
  tmp="$(mktemp -d -t claude-parachute)"
  fetch "$tmp" "$1"
  ok "release $VERSION is fine on this Mac; nothing was installed"
  rm -rf -- "${tmp:?}"
}

write_plist() {
  mkdir -p "$(dirname "$PLIST")" "$(dirname "$LOG")"
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key>
	<string>$LABEL</string>
	<key>ProgramArguments</key>
	<array>
		<string>$BIN</string>
	</array>
	<key>RunAtLoad</key>
	<true/>
	<key>KeepAlive</key>
	<dict>
		<key>SuccessfulExit</key>
		<false/>
	</dict>
	<key>LimitLoadToSessionType</key>
	<string>Aqua</string>
	<key>ProcessType</key>
	<string>Interactive</string>
	<key>ThrottleInterval</key>
	<integer>10</integer>
	<key>StandardOutPath</key>
	<string>$LOG</string>
	<key>StandardErrorPath</key>
	<string>$LOG</string>
</dict>
</plist>
EOF
  plutil -lint "$PLIST" >/dev/null
}

do_install() {
  preflight
  local tmp
  tmp="$(mktemp -d -t claude-parachute)"
  fetch "$tmp" "$1"
  if loaded; then launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true; sleep 1; fi
  mkdir -p "$APP_DIR" "$SUPPORT"
  rm -rf -- "${APP:?}"
  ditto "$tmp/$APP_NAME" "$APP"
  rm -rf -- "${tmp:?}"
  ok "app: $APP"
  # remember the wallpaper you had (once: an update keeps the original), then show a still frame of the scene
  if [ ! -f "$BACKUP" ] && [ -f "$WP_STORE" ]; then cp "$WP_STORE" "$BACKUP"; ok "your current wallpaper setting is saved"; fi
  cp "$APP/Contents/Resources/wallpaper.png" "$POSTER"
  "$BIN" --set-wallpaper "$POSTER" >/dev/null 2>&1 || echo "  (could not set the still wallpaper; the animation works anyway)"
  write_plist
  launchctl enable "$DOMAIN/$LABEL"
  launchctl bootstrap "$DOMAIN" "$PLIST"
  ok "starts at login ($PLIST)"
  sleep 2
  if loaded; then
    say "Claude Parachute $VERSION is running."
  else
    die "the LaunchAgent did not start; see $LOG"
  fi
  cat <<EOF
${D}It animates while the desktop is in front (click the desktop, or hide your apps) and stands still, using no
CPU, while you work in an app. macOS may show a "Background Items Added" notice: that is this LaunchAgent.
  Pause / resume:   curl -fsSL https://raw.githubusercontent.com/$REPO/main/install.sh | bash -s -- pause
  Always animate:   ... | bash -s -- mode visible
  Uninstall:        ... | bash -s -- uninstall${N}
EOF
}

do_uninstall() {
  if loaded; then launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true; fi
  launchctl enable "$DOMAIN/$LABEL" 2>/dev/null || true
  rm -f -- "$PLIST"
  rm -rf -- "${APP:?}"
  defaults delete "$LABEL" >/dev/null 2>&1 || true
  if [ -f "$BACKUP" ]; then
    cp "$BACKUP" "$WP_STORE"
    killall WallpaperAgent 2>/dev/null || true
    ok "previous wallpaper restored"
  fi
  rm -rf -- "${SUPPORT:?}"
  rm -f -- "$LOG"
  ok "Claude Parachute is uninstalled"
}

do_status() {
  if [ ! -f "$PLIST" ]; then echo "not installed"; return 0; fi
  if ! loaded; then echo "installed, not running ('start' turns it on)"; return 0; fi
  local info pid
  info="$(launchctl print "$DOMAIN/$LABEL")"
  printf '%s\n' "$info" | grep -E $'^\t(state|pid|last exit code) =' | sed $'s/^\t//'
  pid="$(printf '%s\n' "$info" | awk -F' = ' $'/^\tpid = / {print $2}')"
  if [ -n "$pid" ]; then
    ps -o %cpu=,rss=,etime= -p "$pid" | awk '{printf "cpu %s%%  memory %.0f MB  uptime %s\n", $1, $2/1024, $3}'
    grep -E ' (play|pause) \(' "$LOG" 2>/dev/null | tail -n 1 | awk '{print "now: " $3 " (since " $2 ")"}'
  fi
  echo "mode: $(defaults read "$LABEL" PlayWhen 2>/dev/null || echo desktop), on battery: $( [ "$(defaults read "$LABEL" PauseOnBattery 2>/dev/null || echo 0)" = 1 ] && echo pause || echo play)"
}

main "$@"
