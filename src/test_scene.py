"""Tests for the wallpaper scene: the loop is seamless, the story is continuous, traffic never overlaps.

Run: .venv/bin/python -m unittest discover -s render -v
"""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scene  # noqa: E402

FPS = 60


class SceneTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = scene.Renderer()
        cls.city_only = scene.Renderer(with_story=False)

    def test_frame_shape(self):
        f = self.r.frame(12.3)
        self.assertEqual(f.shape, (scene.ART_H, scene.ART_W, 3))
        self.assertEqual(f.dtype, np.uint8)

    def test_loop_is_seamless(self):
        """Every periodic element completes whole cycles: t = LOOP renders exactly like t = 0."""
        a = self.r.frame(0.0)
        b = self.r.frame(scene.LOOP, wrap=False)
        self.assertTrue(np.array_equal(a, b), "frame(LOOP) differs from frame(0) in %d pixels" % int((a != b).any(2).sum()))

    def test_city_is_periodic_without_wrapping(self):
        """Without wrapping t, the city alone (clouds, traffic, lights, water) repeats exactly every LOOP."""
        for t in (3.7, 21.05, 44.4):
            a = self.city_only.frame(t, wrap=False)
            b = self.city_only.frame(t + scene.LOOP, wrap=False)
            self.assertTrue(np.array_equal(a, b), "city not periodic at t=%.2f: %d px" % (t, int((a != b).any(2).sum())))

    def test_story_is_off_screen_at_the_seam(self):
        """Around the seam the story draws nothing, so the loop restarts without a pop."""
        for t in (0.0, 1.0 / FPS, scene.LOOP - 1.0 / FPS, scene.LOOP - 0.5):
            a = self.r.frame(t)
            b = self.city_only.frame(t)
            self.assertTrue(np.array_equal(a, b), "story visible at t=%.3f" % t)

    def test_seam_step_is_like_any_other_step(self):
        """The change from the last frame to the first is no bigger than a typical frame-to-frame change."""
        last, first = self.r.frame(scene.LOOP - 1.0 / FPS), self.r.frame(0.0)
        seam = int((last != first).any(2).sum())
        # every transition during one second of the city alone (cloud layers step together every few frames)
        frames = [self.city_only.frame(30.0 + i / FPS) for i in range(FPS + 1)]
        steps = [int((a != b).any(2).sum()) for a, b in zip(frames, frames[1:])]
        self.assertLessEqual(seam, max(steps) * 1.2, "seam %d vs typical max %d" % (seam, max(steps)))

    def test_bands_behind_menu_bar_and_dock_stay_still(self):
        """The player shows frame 0 behind the menu bar and the Dock: those rows must be identical in every frame."""
        f0 = self.r.frame(0.0)
        for i in range(0, int(scene.LOOP * FPS), 6):          # every 0.1 s, story included
            f = self.r.frame(i / FPS)
            top = (f[:scene.STILL_TOP] != f0[:scene.STILL_TOP]).any(2)
            bottom = (f[scene.STILL_BOTTOM:] != f0[scene.STILL_BOTTOM:]).any(2)
            self.assertFalse(top.any(), "menu-bar band changes at t=%.2f, cols %s" % (i / FPS, np.where(top.any(0))[0][:6]))
            self.assertFalse(bottom.any(), "Dock band changes at t=%.2f, rows %s" % (
                i / FPS, scene.STILL_BOTTOM + np.where(bottom.any(1))[0][:6]))

    def test_deterministic(self):
        other = scene.Renderer()
        self.assertTrue(np.array_equal(self.r.frame(17.31), other.frame(17.31)))


class StoryTest(unittest.TestCase):
    def setUp(self):
        self.s = scene.Story()

    def test_lands_on_the_roof(self):
        st = self.s.claude_state(self.s.T_LAND + 0.01)
        self.assertEqual(tuple(map(int, st["pos"])), (self.s.land_x, scene.ROOF_Y - 1))
        st = self.s.claude_state(self.s.T_LAND - 0.01)
        self.assertLess(abs(st["pos"][0] - self.s.land_x), 2.0)
        self.assertLess(abs(st["pos"][1] - (scene.ROOF_Y - 1)), 2.0)

    def test_claude_never_teleports(self):
        """Feet move at most a few art pixels per frame through every phase change (door, freefall, canopy...)."""
        prev = None
        for i in range(int(scene.LOOP * FPS)):
            t = i / FPS
            st = self.s.claude_state(t)
            if st is None:
                prev = None
                continue
            x, y = st["pos"]
            if prev is not None and prev[2] == st["layer"]:
                d = max(abs(x - prev[0]), abs(y - prev[1]))
                self.assertLess(d, 4.0, "jump of %.1f px at t=%.3f (%s)" % (d, t, st["layer"]))
            prev = (x, y, st["layer"])

    def test_canopy_follows_claude(self):
        """Under canopy the lines are short and the canopy stays above Claude."""
        for i in range(int(self.s.T_OPEN * 10), int(self.s.T_LAND * 10)):
            t = i / 10
            st = self.s.claude_state(t)
            spr, gx, gy = st["canopy"]
            fx, fy = st["pos"]
            self.assertLess(gy + spr.h, fy - 12, "canopy not above Claude at t=%.1f" % t)
            self.assertLess(abs(gx + spr.w / 2 - fx), 12, "canopy drifted away at t=%.1f" % t)

    def test_helicopter_enters_and_leaves(self):
        self.assertLess(self.s.heli_x(0.0) + 90, 0)         # off screen left at the start
        self.assertGreater(self.s.heli_x(16.0), scene.ART_W)  # gone to the right after the drop
        self.assertIsNone(self.s.heli_state(30.0))

    def test_story_ends_off_stage(self):
        for t in (40.5, 45.0, 49.0, 59.9):
            self.assertIsNone(self.s.claude_state(t), "Claude visible at t=%.1f" % t)


class TrafficTest(unittest.TestCase):
    def test_no_two_cars_overlap_in_a_lane(self):
        cars = scene.traffic()
        for i in range(int(scene.LOOP * 20)):
            t = i / 20
            for lane in ("up", "lo"):
                xs = sorted(c.x(t) for c in cars if c.lane == lane and c.visible(t))
                for a, b in zip(xs, xs[1:]):
                    self.assertGreaterEqual(b - a, scene.CAR_W + 2, "cars overlap in lane %s at t=%.2f" % (lane, t))

    def test_taxi_waits_while_claude_gets_in(self):
        taxi = [c for c in scene.traffic() if c.profile][0]
        x0 = taxi.x(47.4)
        x1 = taxi.x(48.2)
        self.assertEqual(x0, x1)
        self.assertLessEqual(x0, scene.LOBBY_DOOR[0] + 18)
        self.assertGreaterEqual(x0 + scene.CAR_W, scene.LOBBY_DOOR[2] + 12)


class SpriteTest(unittest.TestCase):
    def test_all_claude_poses_build(self):
        for arms in scene.ARMS:
            for legs in list(scene.LEGS) + ["spread"]:
                for eyes in scene.EYES:
                    spr, ax, ay = scene.claude(arms, legs, eyes)
                    self.assertTrue(spr.mask.any())
                    if legs in scene.GROUNDED or legs == "spread":
                        self.assertTrue(spr.mask[ay].any(), "no feet on the anchor row for %s/%s" % (arms, legs))

    def test_canopy_attach_points_are_on_the_canopy(self):
        for w in (7, 21, 45, 49):
            spr, attach = scene.canopy(w)
            for x, y in attach:
                self.assertTrue(spr.mask[y, x])

    def test_helicopter_tilts(self):
        for tilt in range(-3, 4):
            spr, lo, offs = scene.heli(tilt)
            self.assertEqual(len(offs), spr.w)
            self.assertTrue(spr.mask.any())


if __name__ == "__main__":
    unittest.main()
