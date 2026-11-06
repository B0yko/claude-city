// Claude Parachute: plays the looping wallpaper video behind the desktop icons on every screen.
//
// Built to cost next to nothing:
// - The video is the art grid itself (864x560, HEVC 4:4:4, 24 fps, hardware decoded) and the compositor scales it
//   x4 to the panel with nearest-neighbour sampling: crisp pixels, 1/16 of the panel's pixels to decode.
// - A single AVQueuePlayer + AVPlayerLooper feeds every window, so each frame is decoded once.
// - Each screen is covered by tile windows just below the desktop icons (above the system wallpaper, below every app
//   window), on all Spaces, mouse passes through. The work area is 3x2 tiles; the strips behind the menu bar and the
//   Dock are separate tiles that do not count. The animation plays only while a work-area tile is visible, so it
//   stops when windows cover the desktop, even if the translucent menu bar and Dock still show a strip of it.
// - It also pauses while the displays sleep, the screen saver runs, the screen is locked, another user is active,
//   Low Power Mode is on, the Mac runs hot, and (if PauseOnBattery is set) on battery. Paused costs 0% CPU.
// - It never holds a power assertion: displays and the Mac sleep as usual.
//
// - By default (PlayWhen = desktop) it animates only while the desktop is in front: Finder is the active app (you
//   clicked the desktop, or hid or minimised the apps) and a work-area tile is visible. While you work in an app the
//   wallpaper is a still picture, like Apple's own aerial wallpapers on the desktop. macOS reports the wallpaper as
//   visible behind any window that is not marked opaque (translucent sidebars, many Electron apps), so occlusion
//   alone would keep it animating, and compositing, behind your work.
//
// Settings (defaults write local.claudechute.wallpaper KEY VALUE, then restart; ./chute mode / battery do this):
//   PlayWhen         desktop (default) | visible
//   PauseOnBattery   -bool, default false
//   PauseOnLowPower  -bool, default true
//
//   ClaudeChute                      run (what the LaunchAgent starts)
//   ClaudeChute --self-test          check the play/pause rules and the bundled video, exit 0 or 1
//   ClaudeChute --send pause|resume|toggle   control the running instance
//   ClaudeChute --set-wallpaper FILE set FILE as the static desktop picture on every screen
//   ClaudeChute --video FILE         play another video file
//   ClaudeChute --always-play        diagnostics: ignore the pause rules (to measure the cost of playback)
import AppKit
import AVFoundation
import IOKit.ps

let bundleLabel = "local.claudechute.wallpaper"

// MARK: - play/pause rules

struct Inputs: Equatable, CustomStringConvertible {
    var visible = true            // at least one wallpaper window is not fully covered
    var screensAsleep = false
    var sessionActive = true      // fast user switching
    var locked = false
    var lowPower = false
    var pauseOnLowPower = true
    var screenSaver = false
    var hot = false               // thermal state serious or critical
    var onBattery = false
    var pauseOnBattery = false
    var userPaused = false
    var desktopInFront = true     // Finder is the active app: the user is looking at the desktop
    var onlyWithDesktopInFront = true   // PlayWhen = desktop (default) or visible

    var description: String {
        "desktopInFront=\(desktopInFront) visible=\(visible) asleep=\(screensAsleep) session=\(sessionActive) locked=\(locked) saver=\(screenSaver) " +
        "lowPower=\(lowPower) hot=\(hot) battery=\(onBattery) userPaused=\(userPaused)"
    }
}

let alwaysPlay = CommandLine.arguments.contains("--always-play")
let animateStrips = CommandLine.arguments.contains("--animate-strips")   // diagnostics: video behind the bars too

func shouldPlay(_ i: Inputs) -> Bool {
    if alwaysPlay { return true }
    return i.visible && !i.screensAsleep && i.sessionActive && !i.locked && !i.screenSaver && !i.hot && !i.userPaused
        && !(i.lowPower && i.pauseOnLowPower) && !(i.onBattery && i.pauseOnBattery)
        && (i.desktopInFront || !i.onlyWithDesktopInFront)
}

func log(_ s: String) {
    let f = DateFormatter()
    f.dateFormat = "yyyy-MM-dd HH:mm:ss"
    FileHandle.standardError.write("\(f.string(from: Date())) \(s)\n".data(using: .utf8)!)
}

// MARK: - windows

final class WallpaperWindow: NSWindow {
    var counts = true             // does this tile's visibility decide whether to play?
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}

/// Just below the desktop icons. Since Sonoma the system wallpaper is its own window above the classic desktop
/// level, so a window at kCGDesktopWindowLevel would sit behind it.
let wallpaperLevel = NSWindow.Level(rawValue: Int(CGWindowLevelForKey(.desktopIconWindow)) - 1)

/// The scene's still bands (render/scene.py STILL_TOP, STILL_BOTTOM) on the 864x560 video.
let art = (w: CGFloat(864), h: CGFloat(560), stillTop: CGFloat(18), stillBottom: CGFloat(512))

/// Does this menu-bar or Dock strip lie inside a band of the scene that never changes? Then it shows a still
/// picture. A bigger Dock, or a screen with another shape (the video fills it, cropping differently), gets video.
func stripIsStill(_ tile: CGRect, on screen: CGRect) -> Bool {
    let s = max(screen.width / art.w, screen.height / art.h)     // points per art pixel (aspect fill)
    let cut = (art.h * s - screen.height) / 2                     // points of the video cut off at the top
    let top = screen.maxY - tile.maxY, bottom = screen.maxY - tile.minY   // distances from the screen's top edge
    let fullWidth = abs(tile.width - screen.width) < 0.5
    if fullWidth && top < 0.5 && bottom <= art.stillTop * s - cut + 0.01 { return true }
    if fullWidth && abs(tile.minY - screen.minY) < 0.5 && top >= art.stillBottom * s - cut - 0.01 { return true }
    return false
}

struct Tile: Equatable {
    let rect: CGRect
    let counts: Bool
}

/// Cover the screen with tiles: the work area (visibleFrame) in cols x rows tiles that decide playback, and the
/// strips behind the menu bar and the Dock, which do not. Tile edges sit on even points (whole art pixels on a
/// 2x panel). Coordinates are Cocoa screen coordinates.
func layoutTiles(frame f: CGRect, visible v0: CGRect, cols: Int = 3, rows: Int = 2) -> [Tile] {
    let v = v0.intersection(f)
    if v.isNull || v.width < 40 || v.height < 40 { return [Tile(rect: f, counts: true)] }
    var out: [Tile] = []
    func add(_ r: CGRect, _ counts: Bool) {
        if r.width >= 1 && r.height >= 1 { out.append(Tile(rect: r, counts: counts)) }
    }
    add(CGRect(x: f.minX, y: v.maxY, width: f.width, height: f.maxY - v.maxY), false)     // menu bar
    add(CGRect(x: f.minX, y: f.minY, width: f.width, height: v.minY - f.minY), false)     // Dock at the bottom
    add(CGRect(x: f.minX, y: v.minY, width: v.minX - f.minX, height: v.height), false)    // Dock on the left
    add(CGRect(x: v.maxX, y: v.minY, width: f.maxX - v.maxX, height: v.height), false)    // Dock on the right
    func cuts(_ a: CGFloat, _ b: CGFloat, _ n: Int) -> [CGFloat] {
        var out: [CGFloat] = [a]
        for i in 1..<n {
            let x: CGFloat = a + (b - a) * CGFloat(i) / CGFloat(n)
            out.append((x / 2).rounded() * 2)
        }
        out.append(b)
        return out
    }
    let xs = cuts(v.minX, v.maxX, cols), ys = cuts(v.minY, v.maxY, rows)
    for r in 0..<rows {
        for c in 0..<cols {
            add(CGRect(x: xs[c], y: ys[r], width: xs[c + 1] - xs[c], height: ys[r + 1] - ys[r]), true)
        }
    }
    return out
}

final class Controller: NSObject {
    let player = AVQueuePlayer()
    var looper: AVPlayerLooper?
    var windows: [WallpaperWindow] = []
    var inputs = Inputs()
    let posterURL: URL?
    var activity: NSObjectProtocol?
    var rebuildPending = false
    var tokens: [NSObjectProtocol] = []
    var windowTokens: [NSObjectProtocol] = []
    var readyWatch: [NSKeyValueObservation] = []
    var powerSource: CFRunLoopSource?

    init(video: URL, poster: URL?) {
        posterURL = poster
        super.init()
        let item = AVPlayerItem(asset: AVURLAsset(url: video))
        item.preferredForwardBufferDuration = 1          // a local file: no need to keep seconds of frames around
        looper = AVPlayerLooper(player: player, templateItem: item)
        player.isMuted = true
        player.allowsExternalPlayback = false
        player.preventsDisplaySleepDuringVideoPlayback = false   // the displays must still go to sleep
        player.automaticallyWaitsToMinimizeStalling = false
        let d = UserDefaults.standard
        inputs.pauseOnLowPower = d.object(forKey: "PauseOnLowPower") as? Bool ?? true
        inputs.pauseOnBattery = d.bool(forKey: "PauseOnBattery")
        inputs.onlyWithDesktopInFront = (d.string(forKey: "PlayWhen") ?? "desktop") != "visible"
        inputs.desktopInFront = Controller.finderInFront()
        inputs.lowPower = ProcessInfo.processInfo.isLowPowerModeEnabled
        inputs.hot = Controller.isHot()
        if inputs.pauseOnBattery {                        // only listen to the power source when the rule is on
            inputs.onBattery = Controller.onBattery()
            sharedController = self
            if let src = IOPSNotificationCreateRunLoopSource({ _ in powerSourceChanged() }, nil)?.takeRetainedValue() {
                CFRunLoopAddSource(CFRunLoopGetMain(), src, .defaultMode)
                powerSource = src
            }
        }
        buildWindows()
        observe()
        apply()
    }

    static func finderInFront() -> Bool {
        NSWorkspace.shared.frontmostApplication?.bundleIdentifier == "com.apple.finder"
    }

    static func isHot() -> Bool {
        let s = ProcessInfo.processInfo.thermalState
        return s == .serious || s == .critical
    }

    static func onBattery() -> Bool {
        guard let snap = IOPSCopyPowerSourcesInfo()?.takeRetainedValue(),
              let type = IOPSGetProvidingPowerSourceType(snap)?.takeUnretainedValue() else { return false }
        return (type as String) == kIOPMBatteryPowerKey
    }

    static func nearest(_ layer: CALayer) {
        layer.magnificationFilter = .nearest
        layer.sublayers?.forEach(nearest)
    }

    func buildWindows() {
        for w in windows { w.orderOut(nil) }
        windows.removeAll()
        windowTokens.forEach(NotificationCenter.default.removeObserver)
        windowTokens.removeAll()
        readyWatch.removeAll()
        // the poster (the art grid) covers the first moments before the first video frame, then it is dropped
        var poster: CGImage?
        if let p = posterURL, let src = CGImageSourceCreateWithURL(p as CFURL, nil) {
            poster = CGImageSourceCreateImageAtIndex(src, 0, nil)
        }
        var summary: [String] = []
        for screen in NSScreen.screens {
            let tiles = layoutTiles(frame: screen.frame, visible: screen.visibleFrame)
            for tile in tiles {
                let w = makeWindow(screen: screen, tile: tile, poster: poster)
                windows.append(w)
                windowTokens.append(NotificationCenter.default.addObserver(
                    forName: NSWindow.didChangeOcclusionStateNotification, object: w, queue: .main) { [weak self] _ in
                        self?.refreshVisibility()
                    })
            }
            summary.append("\(Int(screen.frame.width))x\(Int(screen.frame.height))@\(Int(screen.backingScaleFactor))x " +
                           "\(tiles.filter { $0.counts }.count)+\(tiles.filter { !$0.counts }.count) tiles")
        }
        log("windows: " + summary.joined(separator: ", ") + ", ids " + windows.map { "\($0.windowNumber)" }.joined(separator: " "))
        refreshVisibility()
    }

    /// One tile of the wallpaper: a window over `tile.rect` showing its part of the full-screen video.
    func makeWindow(screen: NSScreen, tile: Tile, poster: CGImage?) -> WallpaperWindow {
        let w = WallpaperWindow(contentRect: tile.rect, styleMask: .borderless, backing: .buffered, defer: false)
        w.counts = tile.counts
        w.setFrame(tile.rect, display: false)
        w.level = wallpaperLevel
        w.collectionBehavior = [.canJoinAllSpaces, .stationary, .ignoresCycle, .fullScreenNone]
        w.ignoresMouseEvents = true
        w.isOpaque = true
        w.hasShadow = false
        w.backgroundColor = .black
        w.isReleasedWhenClosed = false
        w.isRestorable = false
        w.canHide = false
        w.animationBehavior = .none
        w.displaysWhenScreenProfileChanges = true

        let view = NSView(frame: NSRect(origin: .zero, size: tile.rect.size))
        view.wantsLayer = true
        let root = view.layer!
        root.backgroundColor = NSColor.black.cgColor
        root.masksToBounds = true
        // a screen-sized layer, shifted so this window shows its own part of it
        let canvas = CALayer()
        canvas.frame = CGRect(x: screen.frame.minX - tile.rect.minX, y: screen.frame.minY - tile.rect.minY,
                              width: screen.frame.width, height: screen.frame.height)
        canvas.contentsScale = screen.backingScaleFactor
        if let poster {
            canvas.contents = poster
            canvas.contentsGravity = .resizeAspectFill
            canvas.magnificationFilter = .nearest
        }
        root.addSublayer(canvas)
        if !tile.counts && !animateStrips && stripIsStill(tile.rect, on: screen.frame) {
            // behind the menu bar and the Dock the picture stays still: the translucent bars do not have to re-blur
            // the wallpaper 24 times a second, and nothing that moves in the scene passes there
            w.contentView = view
            w.orderFront(nil)
            return w
        }
        let pl = AVPlayerLayer(player: player)
        pl.videoGravity = .resizeAspectFill
        pl.frame = canvas.bounds
        pl.contentsScale = screen.backingScaleFactor
        pl.magnificationFilter = .nearest
        canvas.addSublayer(pl)
        readyWatch.append(pl.observe(\.isReadyForDisplay, options: [.initial, .new]) { layer, _ in
            guard layer.isReadyForDisplay else { return }
            DispatchQueue.main.async {
                // the video sublayers exist now: scale the art-grid frames to the panel without smoothing
                Controller.nearest(layer)
                canvas.contents = nil
                DispatchQueue.main.asyncAfter(deadline: .now() + 1) { Controller.nearest(layer) }
            }
        })
        w.contentView = view
        w.orderFront(nil)
        return w
    }

    func refreshVisibility() {
        // only the work area counts: a strip of wallpaper behind the menu bar or the Dock is not worth animating
        let counting = windows.filter { $0.counts }
        let pool = counting.isEmpty ? windows : counting
        let seen = windows.enumerated().filter { $0.element.occlusionState.contains(.visible) }
            .map { "\($0.offset)\($0.element.counts ? "" : "*")" }
        let desc = seen.joined(separator: ",")
        if desc != lastSeen {
            log("visible tiles: [\(desc)] of \(windows.count) (* = menu bar / Dock strip)")
            lastSeen = desc
        }
        inputs.visible = pool.isEmpty || pool.contains { $0.occlusionState.contains(.visible) }
        apply()
    }

    func observe() {
        let ws = NSWorkspace.shared.notificationCenter
        let on: (NotificationCenter, Notification.Name, @escaping (Controller) -> Void) -> Void = { center, name, f in
            self.tokens.append(center.addObserver(forName: name, object: nil, queue: .main) { [weak self] _ in
                if let self { f(self); self.apply() }
            })
        }
        on(ws, NSWorkspace.screensDidSleepNotification) { $0.inputs.screensAsleep = true }
        on(ws, NSWorkspace.screensDidWakeNotification) { $0.inputs.screensAsleep = false }
        on(ws, NSWorkspace.willSleepNotification) { $0.inputs.screensAsleep = true }
        on(ws, NSWorkspace.didWakeNotification) { $0.inputs.screensAsleep = false }
        on(ws, NSWorkspace.sessionDidResignActiveNotification) { $0.inputs.sessionActive = false }
        on(ws, NSWorkspace.sessionDidBecomeActiveNotification) { $0.inputs.sessionActive = true }
        on(ws, NSWorkspace.activeSpaceDidChangeNotification) { $0.refreshVisibility() }
        on(ws, NSWorkspace.didActivateApplicationNotification) { $0.inputs.desktopInFront = Controller.finderInFront() }
        let dn = DistributedNotificationCenter.default()
        on(dn, Notification.Name("com.apple.screenIsLocked")) { $0.inputs.locked = true }
        on(dn, Notification.Name("com.apple.screenIsUnlocked")) { $0.inputs.locked = false }
        on(dn, Notification.Name("\(bundleLabel).pause")) { $0.inputs.userPaused = true }
        on(dn, Notification.Name("\(bundleLabel).resume")) { $0.inputs.userPaused = false }
        on(dn, Notification.Name("\(bundleLabel).toggle")) { $0.inputs.userPaused.toggle() }
        on(dn, Notification.Name("com.apple.screensaver.didstart")) { $0.inputs.screenSaver = true }
        on(dn, Notification.Name("com.apple.screensaver.didstop")) { $0.inputs.screenSaver = false }
        on(NotificationCenter.default, Notification.Name.NSProcessInfoPowerStateDidChange) {
            $0.inputs.lowPower = ProcessInfo.processInfo.isLowPowerModeEnabled
        }
        on(NotificationCenter.default, ProcessInfo.thermalStateDidChangeNotification) { $0.inputs.hot = Controller.isHot() }
        tokens.append(NotificationCenter.default.addObserver(
            forName: NSApplication.didChangeScreenParametersNotification, object: nil, queue: .main) { [weak self] _ in
                self?.scheduleRebuild()
            })
    }

    func scheduleRebuild() {
        if rebuildPending { return }
        rebuildPending = true
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.6) { [weak self] in
            guard let self else { return }
            self.rebuildPending = false
            self.buildWindows()
        }
    }

    var lastPlaying: Bool?
    var lastSeen = ""

    func apply() {
        let play = shouldPlay(inputs)
        if play {
            player.play()
            if activity == nil {   // keep App Nap from throttling playback; idle system and display sleep still allowed
                activity = ProcessInfo.processInfo.beginActivity(
                    options: [.userInitiatedAllowingIdleSystemSleep], reason: "Animated wallpaper is on screen")
            }
        } else {
            player.pause()
            if let a = activity {
                ProcessInfo.processInfo.endActivity(a)
                activity = nil
            }
        }
        if play != lastPlaying {
            log((play ? "play" : "pause") + " (\(inputs))")
            lastPlaying = play
        }
    }
}

// IOKit calls back through a C function pointer, so the controller is reached through a global
weak var sharedController: Controller?

func powerSourceChanged() {
    guard let c = sharedController else { return }
    c.inputs.onBattery = Controller.onBattery()
    c.apply()
}

// MARK: - command line

let expectedVideo = (width: 864, height: 560, fps: 24.0, seconds: 60.0)

func resourceURL(_ name: String, _ ext: String) -> URL? {
    if let u = Bundle.main.url(forResource: name, withExtension: ext) { return u }
    let local = URL(fileURLWithPath: CommandLine.arguments[0]).deletingLastPathComponent()
        .appendingPathComponent("\(name).\(ext)")
    return FileManager.default.fileExists(atPath: local.path) ? local : nil
}

func argument(_ flag: String) -> String? {
    let a = CommandLine.arguments
    guard let i = a.firstIndex(of: flag), i + 1 < a.count else { return nil }
    return a[i + 1]
}

func selfTest(video: URL?) -> Int32 {
    var failures = 0
    func check(_ ok: Bool, _ what: String) {
        print((ok ? "ok    " : "FAIL  ") + what)
        if !ok { failures += 1 }
    }
    let base = Inputs()
    check(shouldPlay(base), "plays when the desktop is visible")
    var i = base; i.visible = false; check(!shouldPlay(i), "pauses when every window is covered")
    i = base; i.screensAsleep = true; check(!shouldPlay(i), "pauses while the displays sleep")
    i = base; i.sessionActive = false; check(!shouldPlay(i), "pauses when the user switches out")
    i = base; i.locked = true; check(!shouldPlay(i), "pauses on the lock screen")
    i = base; i.userPaused = true; check(!shouldPlay(i), "pauses on request")
    i = base; i.lowPower = true; check(!shouldPlay(i), "pauses in Low Power Mode")
    i.pauseOnLowPower = false; check(shouldPlay(i), "plays in Low Power Mode when that rule is off")
    i = base; i.screenSaver = true; check(!shouldPlay(i), "pauses while the screen saver runs")
    i = base; i.hot = true; check(!shouldPlay(i), "pauses while the Mac runs hot")
    i = base; i.desktopInFront = false; check(!shouldPlay(i), "stands still while another app is in front (PlayWhen=desktop)")
    i.onlyWithDesktopInFront = false; check(shouldPlay(i), "plays behind other apps when PlayWhen=visible")
    i = base; i.onBattery = true; check(shouldPlay(i), "plays on battery by default")
    i.pauseOnBattery = true; check(!shouldPlay(i), "pauses on battery when PauseOnBattery is set")
    check(!alwaysPlay, "rules are on (not --always-play)")
    let screenRect = CGRect(x: 0, y: 0, width: 1728, height: 1117)
    for (name, vis) in [("Dock at the bottom", CGRect(x: 0, y: 89, width: 1728, height: 991)),
                        ("Dock on the left", CGRect(x: 80, y: 0, width: 1648, height: 1080)),
                        ("Dock hidden", CGRect(x: 0, y: 4, width: 1728, height: 1076))] {
        let t = layoutTiles(frame: screenRect, visible: vis)
        let area = t.reduce(0) { $0 + $1.rect.width * $1.rect.height }
        var overlap: CGFloat = 0
        for i in t.indices { for j in t.indices where j > i {
            let x = t[i].rect.intersection(t[j].rect); if !x.isNull { overlap += x.width * x.height } } }
        check(abs(area - screenRect.width * screenRect.height) < 0.5 && overlap < 0.5 && t.filter { $0.counts }.count == 6,
              "tiles cover the screen exactly, 6 work-area tiles (\(name))")
    }
    check(layoutTiles(frame: screenRect, visible: .zero) == [Tile(rect: screenRect, counts: true)],
          "no work area: one tile that counts")
    let mbp = layoutTiles(frame: screenRect, visible: CGRect(x: 0, y: 93, width: 1728, height: 991))
    check(mbp.filter { !$0.counts }.allSatisfy { stripIsStill($0.rect, on: screenRect) },
          "16-inch panel: the menu bar (33 pt) and Dock (93 pt) strips show a still picture")
    let bigDock = layoutTiles(frame: screenRect, visible: CGRect(x: 0, y: 140, width: 1728, height: 944))
    check(!stripIsStill(bigDock.first { !$0.counts && $0.rect.minY == 0 }!.rect, on: screenRect),
          "a bigger Dock (140 pt) strip keeps the video")
    let wide = CGRect(x: 0, y: 0, width: 1920, height: 1080)
    check(!stripIsStill(CGRect(x: 0, y: 1056, width: 1920, height: 24), on: wide),
          "a 16:9 screen's menu bar strip keeps the video (the crop differs)")
    guard let video else {
        check(false, "bundled video found")
        return 1
    }
    check(true, "bundled video found: \(video.lastPathComponent)")
    let asset = AVURLAsset(url: video)
    let done = DispatchSemaphore(value: 0)
    var info: (Double, CGSize, Float, Bool)?
    Task.detached {
        defer { done.signal() }
        guard let d = try? await asset.load(.duration),
              let t = try? await asset.loadTracks(withMediaType: .video).first,
              let size = try? await t.load(.naturalSize),
              let fps = try? await t.load(.nominalFrameRate),
              let playable = try? await asset.load(.isPlayable) else { return }
        info = (d.seconds, size, fps, playable)
    }
    done.wait()
    if let (dur, size, fps, playable) = info {
        check(playable, "video is playable by AVFoundation")
        let e = expectedVideo
        check(abs(dur - e.seconds) < 0.05, String(format: "loop is %.0f s (%.3f s)", e.seconds, dur))
        check(Int(size.width) == e.width && Int(size.height) == e.height,
              "video is \(e.width)x\(e.height), the art grid (\(Int(size.width))x\(Int(size.height)))")
        check(abs(Double(fps) - e.fps) < 0.01, "video is \(Int(e.fps)) fps (\(fps))")
    } else {
        check(false, "video metadata readable")
    }
    print(failures == 0 ? "self-test passed" : "self-test failed: \(failures)")
    return failures == 0 ? 0 : 1
}

let videoURL: URL? = argument("--video").map { URL(fileURLWithPath: $0) } ?? resourceURL("claude-parachute", "mp4")

if CommandLine.arguments.contains("--self-test") {
    exit(selfTest(video: videoURL))
}
if let cmd = argument("--send") {
    DistributedNotificationCenter.default().postNotificationName(
        Notification.Name("\(bundleLabel).\(cmd)"), object: nil, userInfo: nil, deliverImmediately: true)
    exit(0)
}
if let file = argument("--set-wallpaper") {
    let url = URL(fileURLWithPath: file)
    var ok = true
    for screen in NSScreen.screens {
        do {
            try NSWorkspace.shared.setDesktopImageURL(url, for: screen, options: [
                .imageScaling: NSNumber(value: NSImageScaling.scaleProportionallyUpOrDown.rawValue),
                .allowClipping: true,
            ])
        } catch {
            ok = false
            log("set wallpaper failed on \(screen.localizedName): \(error.localizedDescription)")
        }
    }
    exit(ok ? 0 : 1)
}

guard let video = videoURL else {
    log("no video: put claude-parachute.mp4 in the app's Resources or pass --video FILE")
    exit(2)
}

// one instance only (a manual launch next to the LaunchAgent would stack two windows per screen)
if let id = Bundle.main.bundleIdentifier,
   NSRunningApplication.runningApplications(withBundleIdentifier: id).count > 1 {
    log("already running, exiting")
    exit(0)
}

// launchd appends stderr to ~/Library/Logs/ClaudeChute.log: keep it small
var logStat = stat()
if fstat(2, &logStat) == 0, (logStat.st_mode & S_IFMT) == S_IFREG, logStat.st_size > 512_000 {
    ftruncate(2, 0)
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)

// launchd stops the agent with SIGTERM: exit 0 so the last exit status stays clean
signal(SIGTERM, SIG_IGN)
let sigterm = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
sigterm.setEventHandler { log("SIGTERM, exiting"); exit(0) }
sigterm.resume()

log("start: \(video.path)")
let controller = Controller(video: video, poster: resourceURL("poster", "png"))
app.run()
