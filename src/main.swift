// Claude City: plays the wallpaper video behind the desktop icons.
//
// The video is the 864x560 pixel art itself (HEVC 4:4:4, 24 fps); the compositor scales it up with nearest-neighbour
// sampling. Each screen is covered by tile windows just below the desktop icons: 3x2 tiles over the work area, and
// still strips behind the menu bar and the Dock (nothing moves there in the scene, so the translucent bars need no
// re-blur). It plays only while Finder is in front and a work-area tile is visible, and pauses while the displays
// sleep, the screen saver runs, the screen is locked, another user is active, Low Power Mode is on or the Mac runs
// hot. It never holds a power assertion.
//
//   ClaudeCity                       run (the LaunchAgent starts this)
//   ClaudeCity --send pause|resume   control the running instance
//   ClaudeCity --set-wallpaper FILE  set FILE as the desktop picture on every screen
//   ClaudeCity --self-test           check the rules, the tile layout and the bundled video
import AppKit
import AVFoundation

let label = "local.claudecity.wallpaper"

func log(_ s: String) {
    let f = DateFormatter()
    f.dateFormat = "yyyy-MM-dd HH:mm:ss"
    FileHandle.standardError.write("\(f.string(from: Date())) \(s)\n".data(using: .utf8)!)
}

// MARK: - when to play

struct State: Equatable {
    var finderInFront = true, visible = true, asleep = false, sessionActive = true, locked = false
    var screenSaver = false, lowPower = false, hot = false, paused = false

    var blockers: [String] {
        [(!finderInFront, "app in front"), (!visible, "covered"), (asleep, "asleep"), (!sessionActive, "user switched"),
         (locked, "locked"), (screenSaver, "screen saver"), (lowPower, "low power"), (hot, "hot"), (paused, "paused")]
            .filter { $0.0 }.map { $0.1 }
    }
    var shouldPlay: Bool { blockers.isEmpty }
}

// MARK: - tiles

/// The video's art grid and the bands of the scene that never change (src/scene.py STILL_TOP, STILL_BOTTOM).
let art = (w: CGFloat(864), h: CGFloat(560), stillTop: CGFloat(18), stillBottom: CGFloat(512))

struct Tile: Equatable {
    let rect: CGRect
    let counts: Bool   // does its visibility decide whether to play? (the menu bar and Dock strips do not)
}

/// Cover a screen (Cocoa coordinates) with the work area in cols x rows tiles plus the strips around it. Inner edges
/// sit on even points: whole art pixels on a 2x panel.
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
        var c: [CGFloat] = [a]
        for i in 1..<n {
            let x: CGFloat = a + (b - a) * CGFloat(i) / CGFloat(n)
            c.append((x / 2).rounded() * 2)
        }
        return c + [b]
    }
    let xs = cuts(v.minX, v.maxX, cols), ys = cuts(v.minY, v.maxY, rows)
    for r in 0..<rows {
        for c in 0..<cols {
            add(CGRect(x: xs[c], y: ys[r], width: xs[c + 1] - xs[c], height: ys[r + 1] - ys[r]), true)
        }
    }
    return out
}

/// Can this menu-bar or Dock strip show a still picture? Only if it lies inside a band of the scene that never
/// changes, given how the video fills this screen. A bigger Dock or another screen shape gets video.
func stripIsStill(_ tile: CGRect, on screen: CGRect) -> Bool {
    let s = max(screen.width / art.w, screen.height / art.h)     // points per art pixel (aspect fill)
    let cut = (art.h * s - screen.height) / 2                     // points of video cut off at the top
    let top = screen.maxY - tile.maxY, bottom = screen.maxY - tile.minY
    guard abs(tile.width - screen.width) < 0.5 else { return false }
    if top < 0.5 { return bottom <= art.stillTop * s - cut + 0.01 }
    if abs(tile.minY - screen.minY) < 0.5 { return top >= art.stillBottom * s - cut - 0.01 }
    return false
}

// MARK: - the wallpaper

final class TileWindow: NSWindow {
    var counts = true
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}

final class Wallpaper: NSObject {
    let player = AVQueuePlayer()
    var looper: AVPlayerLooper?
    let posterURL: URL?
    var state = State()
    var windows: [TileWindow] = []
    var observers: [NSObjectProtocol] = []
    var windowObservers: [NSObjectProtocol] = []
    var readyWatch: [NSKeyValueObservation] = []
    var activity: NSObjectProtocol?
    var wasPlaying: Bool?
    var rebuildPending = false

    init(video: URL, poster: URL?) {
        posterURL = poster
        super.init()
        let item = AVPlayerItem(asset: AVURLAsset(url: video))
        item.preferredForwardBufferDuration = 1
        looper = AVPlayerLooper(player: player, templateItem: item)
        player.isMuted = true
        player.allowsExternalPlayback = false
        player.preventsDisplaySleepDuringVideoPlayback = false
        player.automaticallyWaitsToMinimizeStalling = false
        state.finderInFront = Wallpaper.finderInFront()
        state.lowPower = ProcessInfo.processInfo.isLowPowerModeEnabled
        state.hot = Wallpaper.isHot()
        buildWindows()
        observe()
    }

    static func finderInFront() -> Bool { NSWorkspace.shared.frontmostApplication?.bundleIdentifier == "com.apple.finder" }

    static func isHot() -> Bool { [.serious, .critical].contains(ProcessInfo.processInfo.thermalState) }

    static func nearest(_ layer: CALayer) {
        layer.magnificationFilter = .nearest
        layer.sublayers?.forEach(nearest)
    }

    func buildWindows() {
        windows.forEach { $0.orderOut(nil) }
        windows.removeAll()
        windowObservers.forEach(NotificationCenter.default.removeObserver)
        windowObservers.removeAll()
        readyWatch.removeAll()
        var poster: CGImage?
        if let p = posterURL, let src = CGImageSourceCreateWithURL(p as CFURL, nil) {
            poster = CGImageSourceCreateImageAtIndex(src, 0, nil)
        }
        for screen in NSScreen.screens {
            for tile in layoutTiles(frame: screen.frame, visible: screen.visibleFrame) {
                let w = makeWindow(screen: screen, tile: tile, poster: poster)
                windows.append(w)
                windowObservers.append(NotificationCenter.default.addObserver(
                    forName: NSWindow.didChangeOcclusionStateNotification, object: w, queue: .main) { [weak self] _ in
                        self?.refresh()
                    })
            }
        }
        log("\(NSScreen.screens.count) screen(s), \(windows.count) tiles")
        refresh()
    }

    /// A borderless window over `tile` showing its part of a screen-sized picture: still (the poster) or the video.
    func makeWindow(screen: NSScreen, tile: Tile, poster: CGImage?) -> TileWindow {
        let w = TileWindow(contentRect: tile.rect, styleMask: .borderless, backing: .buffered, defer: false)
        w.counts = tile.counts
        w.setFrame(tile.rect, display: false)
        // just below the desktop icons: above the system wallpaper (its own window since Sonoma), below every app
        w.level = NSWindow.Level(rawValue: Int(CGWindowLevelForKey(.desktopIconWindow)) - 1)
        w.collectionBehavior = [.canJoinAllSpaces, .stationary, .ignoresCycle, .fullScreenNone]
        w.ignoresMouseEvents = true
        w.isOpaque = true
        w.hasShadow = false
        w.backgroundColor = .black
        w.isReleasedWhenClosed = false
        w.isRestorable = false
        w.canHide = false
        w.animationBehavior = .none

        let view = NSView(frame: NSRect(origin: .zero, size: tile.rect.size))
        view.wantsLayer = true
        view.layer!.masksToBounds = true
        let canvas = CALayer()
        canvas.frame = CGRect(x: screen.frame.minX - tile.rect.minX, y: screen.frame.minY - tile.rect.minY,
                              width: screen.frame.width, height: screen.frame.height)
        canvas.contentsScale = screen.backingScaleFactor
        canvas.contents = poster
        canvas.contentsGravity = .resizeAspectFill
        canvas.magnificationFilter = .nearest
        view.layer!.addSublayer(canvas)
        if tile.counts || !stripIsStill(tile.rect, on: screen.frame) {
            let pl = AVPlayerLayer(player: player)
            pl.videoGravity = .resizeAspectFill
            pl.frame = canvas.bounds
            pl.contentsScale = screen.backingScaleFactor
            canvas.addSublayer(pl)
            readyWatch.append(pl.observe(\.isReadyForDisplay, options: [.initial, .new]) { layer, _ in
                guard layer.isReadyForDisplay else { return }
                DispatchQueue.main.async {
                    Wallpaper.nearest(layer)             // the video's own sublayers exist only now
                    canvas.contents = nil
                    DispatchQueue.main.asyncAfter(deadline: .now() + 1) { Wallpaper.nearest(layer) }
                }
            })
        }
        w.contentView = view
        w.orderFront(nil)
        return w
    }

    func refresh() {
        let work = windows.filter { $0.counts }
        state.visible = (work.isEmpty ? windows : work).contains { $0.occlusionState.contains(.visible) }
        apply()
    }

    func observe() {
        let ws = NSWorkspace.shared.notificationCenter, dn = DistributedNotificationCenter.default()
        let nc = NotificationCenter.default
        func on(_ center: NotificationCenter, _ name: Notification.Name, _ f: @escaping (Wallpaper) -> Void) {
            observers.append(center.addObserver(forName: name, object: nil, queue: .main) { [weak self] _ in
                if let self { f(self); self.refresh() }
            })
        }
        on(ws, NSWorkspace.didActivateApplicationNotification) { $0.state.finderInFront = Wallpaper.finderInFront() }
        on(ws, NSWorkspace.screensDidSleepNotification) { $0.state.asleep = true }
        on(ws, NSWorkspace.screensDidWakeNotification) { $0.state.asleep = false }
        on(ws, NSWorkspace.willSleepNotification) { $0.state.asleep = true }
        on(ws, NSWorkspace.didWakeNotification) { $0.state.asleep = false }
        on(ws, NSWorkspace.sessionDidResignActiveNotification) { $0.state.sessionActive = false }
        on(ws, NSWorkspace.sessionDidBecomeActiveNotification) { $0.state.sessionActive = true }
        on(ws, NSWorkspace.activeSpaceDidChangeNotification) { _ in }
        on(dn, Notification.Name("com.apple.screenIsLocked")) { $0.state.locked = true }
        on(dn, Notification.Name("com.apple.screenIsUnlocked")) { $0.state.locked = false }
        on(dn, Notification.Name("com.apple.screensaver.didstart")) { $0.state.screenSaver = true }
        on(dn, Notification.Name("com.apple.screensaver.didstop")) { $0.state.screenSaver = false }
        on(dn, Notification.Name("\(label).pause")) { $0.state.paused = true }
        on(dn, Notification.Name("\(label).resume")) { $0.state.paused = false }
        on(nc, Notification.Name.NSProcessInfoPowerStateDidChange) {
            $0.state.lowPower = ProcessInfo.processInfo.isLowPowerModeEnabled
        }
        on(nc, ProcessInfo.thermalStateDidChangeNotification) { $0.state.hot = Wallpaper.isHot() }
        observers.append(nc.addObserver(forName: NSApplication.didChangeScreenParametersNotification, object: nil,
                                        queue: .main) { [weak self] _ in self?.scheduleRebuild() })
    }

    func scheduleRebuild() {
        guard !rebuildPending else { return }
        rebuildPending = true
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.6) { [weak self] in
            self?.rebuildPending = false
            self?.buildWindows()
        }
    }

    func apply() {
        let play = state.shouldPlay
        if play {
            player.play()
            // keeps App Nap from throttling playback; idle system and display sleep stay allowed
            activity = activity ?? ProcessInfo.processInfo.beginActivity(
                options: .userInitiatedAllowingIdleSystemSleep, reason: "Animated wallpaper on screen")
        } else {
            player.pause()
            if let a = activity { ProcessInfo.processInfo.endActivity(a); activity = nil }
        }
        if play != wasPlaying {
            log(play ? "play" : "pause (\(state.blockers.joined(separator: ", ")))")
            wasPlaying = play
        }
    }
}

// MARK: - self-test

func selfTest(video: URL?) -> Int32 {
    var failed = 0
    func check(_ ok: Bool, _ what: String) {
        print((ok ? "ok    " : "FAIL  ") + what)
        if !ok { failed += 1 }
    }
    check(State().shouldPlay, "plays with the desktop in front and visible")
    let blockers: [(String, (inout State) -> Void)] = [
        ("another app is in front", { $0.finderInFront = false }), ("the desktop is covered", { $0.visible = false }),
        ("the displays sleep", { $0.asleep = true }), ("another user is active", { $0.sessionActive = false }),
        ("the screen is locked", { $0.locked = true }), ("the screen saver runs", { $0.screenSaver = true }),
        ("Low Power Mode is on", { $0.lowPower = true }), ("the Mac runs hot", { $0.hot = true }),
        ("asked to pause", { $0.paused = true }),
    ]
    for (name, set) in blockers {
        var s = State()
        set(&s)
        check(!s.shouldPlay, "pauses when \(name)")
    }

    let screen = CGRect(x: 0, y: 0, width: 1728, height: 1117)
    for (name, work) in [("Dock at the bottom", CGRect(x: 0, y: 93, width: 1728, height: 991)),
                         ("Dock on the left", CGRect(x: 80, y: 0, width: 1648, height: 1084)),
                         ("Dock hidden", CGRect(x: 0, y: 4, width: 1728, height: 1080))] {
        let t = layoutTiles(frame: screen, visible: work)
        let area = t.reduce(CGFloat(0)) { $0 + $1.rect.width * $1.rect.height }
        var overlap = CGFloat(0)
        for i in t.indices {
            for j in t.indices where j > i {
                let x = t[i].rect.intersection(t[j].rect)
                if !x.isNull { overlap += x.width * x.height }
            }
        }
        check(abs(area - screen.width * screen.height) < 0.5 && overlap < 0.5 && t.filter { $0.counts }.count == 6,
              "tiles cover the screen exactly, 6 in the work area (\(name))")
    }
    let strips = layoutTiles(frame: screen, visible: CGRect(x: 0, y: 93, width: 1728, height: 991)).filter { !$0.counts }
    check(strips.count == 2 && strips.allSatisfy { stripIsStill($0.rect, on: screen) },
          "16-inch panel: the menu bar and Dock strips stay still")
    check(!stripIsStill(CGRect(x: 0, y: 0, width: 1728, height: 140), on: screen), "a bigger Dock strip gets video")

    guard let video else {
        check(false, "the video is in the app")
        return 1
    }
    let asset = AVURLAsset(url: video)
    let done = DispatchSemaphore(value: 0)
    var info: (seconds: Double, size: CGSize, fps: Float, playable: Bool)?
    Task.detached {
        defer { done.signal() }
        guard let d = try? await asset.load(.duration), let playable = try? await asset.load(.isPlayable),
              let t = try? await asset.loadTracks(withMediaType: .video).first,
              let size = try? await t.load(.naturalSize), let fps = try? await t.load(.nominalFrameRate) else { return }
        info = (d.seconds, size, fps, playable)
    }
    done.wait()
    check(info?.playable == true, "the video plays on this Mac")
    check(info.map { abs($0.seconds - 60) < 0.05 && $0.size == CGSize(width: art.w, height: art.h) && abs($0.fps - 24) < 0.01 } == true,
          "the video is a 60 s loop, 864x560, 24 fps")
    print(failed == 0 ? "self-test passed" : "self-test failed: \(failed)")
    return failed == 0 ? 0 : 1
}

// MARK: - main

func argument(_ flag: String) -> String? {
    let a = CommandLine.arguments
    guard let i = a.firstIndex(of: flag), i + 1 < a.count else { return nil }
    return a[i + 1]
}

let video = Bundle.main.url(forResource: "claude-city", withExtension: "mp4")

if CommandLine.arguments.contains("--self-test") {
    exit(selfTest(video: video))
}
if let cmd = argument("--send") {
    DistributedNotificationCenter.default().postNotificationName(
        Notification.Name("\(label).\(cmd)"), object: nil, userInfo: nil, deliverImmediately: true)
    exit(0)
}
if let file = argument("--set-wallpaper") {
    var ok = true
    for screen in NSScreen.screens {
        do {
            try NSWorkspace.shared.setDesktopImageURL(URL(fileURLWithPath: file), for: screen, options: [
                .imageScaling: NSNumber(value: NSImageScaling.scaleProportionallyUpOrDown.rawValue),
                .allowClipping: true,
            ])
        } catch {
            ok = false
            log("could not set the wallpaper on \(screen.localizedName): \(error.localizedDescription)")
        }
    }
    exit(ok ? 0 : 1)
}
guard let video else {
    log("claude-city.mp4 is missing from the app")
    exit(2)
}
if let id = Bundle.main.bundleIdentifier, NSRunningApplication.runningApplications(withBundleIdentifier: id).count > 1 {
    log("already running")
    exit(0)
}

// launchd appends stderr to the log: keep it small
var st = stat()
if fstat(2, &st) == 0, (st.st_mode & S_IFMT) == S_IFREG, st.st_size > 512_000 { ftruncate(2, 0) }

// launchd stops the agent with SIGTERM: exit 0 so its last exit status stays clean
signal(SIGTERM, SIG_IGN)
let sigterm = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
sigterm.setEventHandler { exit(0) }
sigterm.resume()

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
log("start")
let wallpaper = Wallpaper(video: video, poster: Bundle.main.url(forResource: "poster", withExtension: "png"))
app.run()
