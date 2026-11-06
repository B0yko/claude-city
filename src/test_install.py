"""install.sh in a sandbox: a fake HOME, stubs for launchctl/killall/defaults and a fake app, so nothing on this Mac
is touched. Checks that an update leaves no old copies behind and that uninstall restores the wallpaper."""
import os
import subprocess
import tempfile
import time
import unittest

INSTALL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "install.sh")
STUB = '#!/bin/sh\necho "$(basename "$0") $*" >> "$HOME/calls.log"\n[ "$1" = print ] && exit 1\nexit 0\n'
FAKE_APP = """#!/bin/sh
case "$1" in
  --self-test) echo "ok    fake"; exit ${SELF_TEST_EXIT:-0} ;;
  --set-wallpaper) echo "$2" >> "$HOME/set-wallpaper.log" ;;
esac
"""


def write(path, text, mode=0o644):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    os.chmod(path, mode)


class InstallerTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.home = os.path.join(self.root, "home")
        self.tmpdir = os.path.join(self.root, "tmp")
        self.stubs = os.path.join(self.root, "stubs")
        os.makedirs(self.tmpdir)
        for tool in ("launchctl", "killall", "defaults"):
            write(os.path.join(self.stubs, tool), STUB, 0o755)
        self.app = os.path.join(self.root, "new", "Claude City.app")
        write(os.path.join(self.app, "Contents/MacOS/ClaudeCity"), FAKE_APP, 0o755)
        write(os.path.join(self.app, "Contents/Resources/wallpaper.png"), "png")
        self.store = self.h("Library/Application Support/com.apple.wallpaper/Store/Index.plist")
        write(self.store, "CURRENT")

    def tearDown(self):
        subprocess.run(["rm", "-rf", self.root])

    def h(self, rel):
        return os.path.join(self.home, rel)

    def run_sh(self, *args, env=None):
        e = {"HOME": self.home, "TMPDIR": self.tmpdir, "PATH": self.stubs + ":/usr/bin:/bin:/usr/sbin"}
        e.update(env or {})
        return subprocess.run(["/bin/bash", INSTALL, *args], env=e, capture_output=True, text=True)

    def read(self, rel):
        with open(self.h(rel)) as f:
            return f.read()

    def calls(self):
        return self.read("calls.log") if os.path.exists(self.h("calls.log")) else ""

    def install_claude_parachute(self):
        """What Claude Parachute 1.x left on a Mac."""
        write(self.h("Applications/Claude Parachute.app/Contents/MacOS/ClaudeChute"), "old", 0o755)
        write(self.h("Library/LaunchAgents/local.claudechute.wallpaper.plist"), "old")
        write(self.h("Library/Logs/ClaudeChute.log"), "old log")
        write(self.h("Library/Application Support/Claude Parachute/wallpaper-Index.plist"), "ORIGINAL")
        write(self.h("Library/Application Support/Claude Parachute/poster.png"), "old poster")
        write(self.store, "OLD POSTER AS WALLPAPER")

    def assert_installed(self):
        self.assertTrue(os.access(self.h("Applications/Claude City.app/Contents/MacOS/ClaudeCity"), os.X_OK))
        plist = self.h("Library/LaunchAgents/local.claudecity.wallpaper.plist")
        self.assertEqual(subprocess.run(["plutil", "-lint", plist], capture_output=True).returncode, 0)
        self.assertIn("Claude City.app/Contents/MacOS/ClaudeCity", self.read("Library/LaunchAgents/local.claudecity.wallpaper.plist"))
        data = sorted(os.listdir(self.h("Library/Application Support/Claude City")))
        posters = [f for f in data if f.startswith("poster")]
        self.assertEqual(len(posters), 1, data)
        self.assertEqual(set(data), {posters[0], "wallpaper-Index.plist"})
        # the system wallpaper was switched to the poster that is on disk (not to a file deleted afterwards)
        last_set = self.read("set-wallpaper.log").splitlines()[-1]
        self.assertEqual(last_set, self.h("Library/Application Support/Claude City/" + posters[0]))
        self.assertIn("bootstrap", self.calls())
        self.assertEqual(os.listdir(self.tmpdir), [], "temporary files left behind")

    def test_update_from_claude_parachute_leaves_nothing_old(self):
        self.install_claude_parachute()
        r = self.run_sh("install", self.app)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assert_installed()
        for old in ("Applications/Claude Parachute.app", "Library/LaunchAgents/local.claudechute.wallpaper.plist",
                    "Library/Logs/ClaudeChute.log", "Library/Application Support/Claude Parachute"):
            self.assertFalse(os.path.exists(self.h(old)), old + " is still there")
        self.assertIn("bootout gui/%d/local.claudechute.wallpaper" % os.getuid(), self.calls())
        # the original wallpaper setting moved over; the current one (already our poster) was not saved instead
        self.assertEqual(self.read("Library/Application Support/Claude City/wallpaper-Index.plist"), "ORIGINAL")

    def test_updating_again_replaces_in_place(self):
        self.assertEqual(self.run_sh("install", self.app).returncode, 0)
        write(self.h("Library/Logs/ClaudeCity.log"), "a long log")
        write(self.h("Applications/Claude City.app/Contents/Resources/stale-file"), "from the previous version")
        write(self.store, "OUR POSTER")
        first = self.read("set-wallpaper.log").splitlines()[-1]
        time.sleep(1.1)                                   # posters are named by the second
        r = self.run_sh("install", self.app)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assert_installed()
        self.assertNotEqual(self.read("set-wallpaper.log").splitlines()[-1], first, "the poster path must change")
        self.assertFalse(os.path.exists(first), "the previous poster should be deleted after switching")
        self.assertFalse(os.path.exists(self.h("Applications/Claude City.app/Contents/Resources/stale-file")))
        self.assertFalse(os.path.exists(self.h("Library/Logs/ClaudeCity.log")), "the old log should go")
        self.assertEqual(self.read("Library/Application Support/Claude City/wallpaper-Index.plist"), "CURRENT")

    def test_uninstall_restores_the_wallpaper_and_removes_everything(self):
        self.install_claude_parachute()
        self.assertEqual(self.run_sh("install", self.app).returncode, 0)
        r = self.run_sh("uninstall")
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(self.store) as f:
            self.assertEqual(f.read(), "ORIGINAL")
        self.assertIn("killall WallpaperAgent", self.calls())
        left = [os.path.join(d, f) for d, _, fs in os.walk(self.home) for f in fs]
        self.assertEqual(sorted(os.path.relpath(p, self.home) for p in left),
                         ["Library/Application Support/com.apple.wallpaper/Store/Index.plist", "calls.log",
                          "set-wallpaper.log"])
        self.assertEqual(os.listdir(self.h("Applications")), [])

    def test_a_failing_self_test_changes_nothing(self):
        self.install_claude_parachute()
        r = self.run_sh("install", self.app, env={"SELF_TEST_EXIT": "1"})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("self-test failed", r.stderr)
        self.assertTrue(os.path.exists(self.h("Applications/Claude Parachute.app")))
        self.assertFalse(os.path.exists(self.h("Applications/Claude City.app")))
        self.assertEqual(os.listdir(self.tmpdir), [], "temporary files left behind")

    def test_status_and_usage(self):
        self.assertIn("not installed", self.run_sh("status").stdout)
        self.install_claude_parachute()
        self.assertIn("earlier version", self.run_sh("status").stdout)
        self.assertNotEqual(self.run_sh("bogus").returncode, 0)


if __name__ == "__main__":
    unittest.main()
