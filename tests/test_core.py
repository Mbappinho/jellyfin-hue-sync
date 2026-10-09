"""Behavioral tests for the dependency-free ambilight core."""

import unittest

from ambilight import core as core_module
from ambilight.core import sample_rgb


class SamplingTests(unittest.TestCase):
    def test_packed_rgb24_is_averaged_per_half(self):
        self.assertIsNotNone(sample_rgb, "sample_rgb must be implemented")
        data = bytes((100, 20, 30, 200, 40, 50, 0, 60, 80, 20, 100, 120))
        self.assertEqual(sample_rgb(data, 4, 1), ((150, 30, 40), (10, 80, 100)))

    def test_entire_near_black_outer_rows_and_columns_are_trimmed(self):
        pixels = []
        for y in range(6):
            for x in range(6):
                pixels.extend((200, 30, 40) if 2 <= y < 4 and x == 2 else
                              (20, 90, 240) if 2 <= y < 4 and x == 3 else
                              (12, 12, 12))
        self.assertEqual(sample_rgb(bytes(pixels), 6, 6),
                         ((200, 30, 40), (20, 90, 240)))

    def test_black_and_single_column_frames_never_divide_by_zero(self):
        for width, height, rgb in ((1, 1, (0, 0, 0)), (1, 4, (90, 30, 10)),
                                   (2, 1, (0, 0, 0)), (9, 9, (0, 0, 0))):
            with self.subTest(width=width, height=height):
                try:
                    actual = sample_rgb(bytes(rgb) * (width * height), width, height)
                except ZeroDivisionError:
                    self.fail("Narrow frames must retain at least one pixel per half")
                self.assertEqual(actual, (rgb, rgb))

    def test_rgb24_rejects_invalid_shapes_and_payloads(self):
        with self.assertRaises(ValueError):
            sample_rgb(bytes(13), 4, 1)
        for width, height in ((0, 1), (1, 0), (-1, 1)):
            with self.subTest(width=width, height=height), self.assertRaises(ValueError):
                sample_rgb(b"", width, height)
        with self.assertRaises(ValueError):
            sample_rgb(bytes(2), 1, 1)
        for width, height in ((True, 1), (1, 1.0)):
            with self.subTest(width=width, height=height), self.assertRaises(TypeError):
                sample_rgb(bytes(3), width, height)
        with self.assertRaises(TypeError):
            sample_rgb([1, 2, 3], 1, 1)

    def test_border_trim_is_capped_at_one_third_per_edge(self):
        rows = [(0, 0, 0)] * 6 + [(180, 60, 30)] * 3
        data = bytes(c for rgb in rows for _ in range(4) for c in rgb)
        self.assertEqual(sample_rgb(data, 4, 9), ((90, 30, 15), (90, 30, 15)))
        data = bytes(c for _ in range(4) for rgb in rows for c in rgb)
        self.assertEqual(sample_rgb(data, 9, 4), ((0, 0, 0), (180, 60, 30)))

    def test_nonblack_pixel_prevents_outer_row_trimming(self):
        data = bytes((0, 0, 0, 0, 0, 0, 13, 0, 0,
                      180, 60, 30, 180, 60, 30, 180, 60, 30,
                      180, 60, 30, 180, 60, 30, 180, 60, 30))
        self.assertEqual(sample_rgb(data, 3, 3), ((120, 40, 20), (122, 40, 20)))

    def test_odd_width_assigns_middle_column_to_right_half(self):
        self.assertEqual(sample_rgb(bytes((90, 30, 20, 60, 90, 40, 100, 50, 80)), 3, 1),
                         ((90, 30, 20), (80, 70, 60)))


class LightColorTests(unittest.TestCase):
    def test_light_positions_select_halves_and_average_center(self):
        fn = getattr(core_module, "colors_for_lights", None)
        self.assertIsNotNone(fn, "colors_for_lights must be implemented")
        self.assertEqual(fn((100, 20, 40), (20, 100, 80),
                            [-1.0, -0.051, -0.05, 0.0, 0.05, 0.051, 1.0], 0.5),
                         [(50, 10, 20), (50, 10, 20), (30, 30, 30),
                          (30, 30, 30), (30, 30, 30), (10, 50, 40), (10, 50, 40)])
        self.assertEqual(fn((100, 20, 40), (20, 100, 80), [-1.0]), [(70, 14, 28)])
        self.assertEqual(fn((100, 20, 40), (20, 100, 80), [], 0.0), [])

    def test_light_mapping_validates_rgb_positions_and_brightness(self):
        fn = core_module.colors_for_lights
        for left, right, xs, brightness in (
            ((256, 0, 0), (0, 0, 0), [0.0], 0.7),
            ((0, -1, 0), (0, 0, 0), [0.0], 0.7),
            ((0, 0), (0, 0, 0), [0.0], 0.7),
            ((0, 0, 0), (0, 0, 0), [float("nan")], 0.7),
            ((0, 0, 0), (0, 0, 0), [1.1], 0.7),
            ((0, 0, 0), (0, 0, 0), [0.0], -0.1),
            ((0, 0, 0), (0, 0, 0), [0.0], 1.1),
            ((0, 0, 0), (0, 0, 0), [], float("inf")),
        ):
            with self.subTest(left=left, xs=xs, brightness=brightness), self.assertRaises(ValueError):
                fn(left, right, xs, brightness)
        for left, xs, brightness in (((True, 0, 0), [0.0], 0.7),
                                     ((0, 0, 0), ["0"], 0.7),
                                     ((0, 0, 0), [True], 0.7),
                                     ((0, 0, 0), (0.0,), 0.7),
                                     ((0, 0, 0), [0.0], True)):
            with self.subTest(left=left, xs=xs), self.assertRaises(TypeError):
                fn(left, (0, 0, 0), xs, brightness)


class PlaybackClockTests(unittest.TestCase):
    @staticmethod
    def session(seconds=10, paused=False, item_id="movie-a"):
        return {"NowPlayingItem": {"Id": item_id},
                "PlayState": {"PositionTicks": round(seconds * 10_000_000),
                              "IsPaused": paused}}

    def clock(self):
        cls = getattr(core_module, "PlaybackClock", None)
        self.assertIsNotNone(cls, "PlaybackClock must be implemented")
        return cls()

    def test_running_movie_extrapolates_ticks_from_monotonic_anchor(self):
        clock = self.clock()
        self.assertIsNone(clock.position(100.0))
        self.assertIsNone(clock.item_id)
        self.assertFalse(clock.active)
        self.assertFalse(clock.paused)
        clock.update(self.session(10.125), 100.0)
        self.assertEqual(clock.item_id, "movie-a")
        self.assertTrue(clock.active)
        self.assertFalse(clock.paused)
        self.assertAlmostEqual(clock.position(102.5), 12.625)

    def test_identical_poll_reports_do_not_pin_the_clock(self):
        clock = self.clock()
        session = self.session(10)
        clock.update(session, 100)
        self.assertEqual(clock.position(103), 13)
        clock.update(self.session(10), 103)
        self.assertEqual(clock.position(104), 14)
        clock.update(session, 107)
        self.assertEqual(clock.position(108), 18)

    def test_fresh_delayed_report_never_moves_running_position_backwards(self):
        clock = self.clock()
        clock.update(self.session(10), 100)
        self.assertEqual(clock.position(105), 15)
        clock.update(self.session(12), 105)
        self.assertEqual(clock.position(105), 15)
        self.assertEqual(clock.position(106), 16)
        clock.update(self.session(40), 107)
        self.assertEqual(clock.position(107), 40)
        self.assertEqual(clock.position(108), 41)

    def test_pause_resume_reanchors_without_rewinding_identical_ticks(self):
        clock = self.clock()
        clock.update(self.session(10), 100)
        self.assertEqual(clock.position(105), 15)
        clock.update(self.session(10, paused=True), 105)
        self.assertTrue(clock.paused)
        self.assertEqual(clock.position(106), 15)
        clock.update(self.session(10, paused=True), 110)
        self.assertEqual(clock.position(200), 15)
        clock.update(self.session(10), 200)
        self.assertFalse(clock.paused)
        self.assertEqual(clock.position(201), 16)

    def test_running_clock_stales_after_twenty_seconds_of_unchanged_reports(self):
        clock = self.clock()
        clock.update(self.session(10), 100)
        clock.update(self.session(10), 119)
        self.assertEqual(clock.position(120), 30)
        self.assertIsNone(clock.position(120.001))
        clock.update(self.session(10), 121)
        self.assertIsNone(clock.position(122))
        self.assertTrue(clock.active)
        self.assertEqual(clock.item_id, "movie-a")
        paused = self.clock()
        paused.update(self.session(10, paused=True), 100)
        self.assertEqual(paused.position(1000), 10)

    def test_changed_report_revives_stale_clock_without_backwards_motion(self):
        clock = self.clock()
        clock.update(self.session(10), 100)
        self.assertEqual(clock.position(120), 30)
        self.assertIsNone(clock.position(121))
        try:
            clock.update(self.session(11), 125)
        except TypeError:
            self.fail("A fresh report must revive a stale clock")
        self.assertEqual(clock.position(125), 30)
        self.assertEqual(clock.position(126), 31)
        self.assertEqual(clock.position(145), 50)
        self.assertIsNone(clock.position(145.001))

    def test_first_missing_session_immediately_resets_all_playback_properties(self):
        for empty in (None, {}, {"NowPlayingItem": None}, {"NowPlayingItem": {}},
                      {"NowPlayingItem": {"Id": ""}}):
            with self.subTest(session=empty):
                clock = self.clock()
                clock.update(self.session(10, paused=True), 100)
                try:
                    clock.update(empty, 101)
                except (TypeError, KeyError):
                    self.fail("A missing playing item must reset rather than raise")
                self.assertIsNone(clock.position(101))
                self.assertIsNone(clock.item_id)
                self.assertFalse(clock.active)
                self.assertFalse(clock.paused)
                clock.update(self.session(1), 102)
                self.assertEqual(clock.position(102), 1)

    def test_timestamps_are_finite_and_monotonic_across_updates_and_reads(self):
        clock = self.clock()
        clock.update(self.session(10), 100)
        clock.position(102)
        for action in (lambda: clock.update(self.session(10), 101),
                       lambda: clock.update(None, 101),
                       lambda: clock.position(101)):
            with self.assertRaises(ValueError):
                action()
        self.assertEqual(clock.position(102), 12)
        for invalid in (float("nan"), float("inf"), -float("inf")):
            for method in ("update", "position"):
                with self.subTest(now=invalid, method=method), self.assertRaises(ValueError):
                    if method == "update":
                        clock.update(self.session(), invalid)
                    else:
                        clock.position(invalid)
        for invalid in (True, "103", None):
            with self.subTest(now=invalid), self.assertRaises(TypeError):
                clock.position(invalid)
        inactive = self.clock()
        self.assertIsNone(inactive.position(10))
        with self.assertRaises(ValueError):
            inactive.update(None, 9)

    def test_malformed_reports_are_rejected_without_mutating_clock(self):
        cases = [
            (self.session(-1), ValueError),
            ([], TypeError),
            ({"NowPlayingItem": "movie-a"}, TypeError),
            (self.session(item_id=123), TypeError),
            ({"NowPlayingItem": {"Id": "movie-a"}}, ValueError),
            ({"NowPlayingItem": {"Id": "movie-a"}, "PlayState": {}}, ValueError),
        ]
        for ticks in (None,):
            value = self.session()
            value["PlayState"]["PositionTicks"] = ticks
            cases.append((value, ValueError))
        for ticks in (True, 100.0, "100"):
            value = self.session()
            value["PlayState"]["PositionTicks"] = ticks
            cases.append((value, TypeError))
        value = self.session()
        value["PlayState"]["IsPaused"] = 1
        cases.append((value, TypeError))
        for invalid, error in cases:
            with self.subTest(session=invalid):
                clock = self.clock()
                clock.update(self.session(10), 100)
                with self.assertRaises(error):
                    clock.update(invalid, 200)
                self.assertEqual(clock.position(101), 11)
                self.assertEqual(clock.item_id, "movie-a")
                self.assertFalse(clock.paused)

    def test_backward_seek_uses_new_report_then_extrapolates(self):
        clock = self.clock()
        clock.update(self.session(40), 100)
        self.assertEqual(clock.position(105), 45)
        clock.update(self.session(3), 105)
        self.assertEqual(clock.position(105), 3)
        self.assertEqual(clock.position(106), 4)
        clock.update(self.session(3), 106)
        self.assertEqual(clock.position(107), 5)

    def test_item_change_discards_previous_timeline_even_with_identical_ticks(self):
        clock = self.clock()
        clock.update(self.session(10), 100)
        self.assertEqual(clock.position(105), 15)
        clock.update(self.session(10, item_id="movie-b"), 105)
        self.assertEqual(clock.item_id, "movie-b")
        self.assertEqual(clock.position(105), 10)
        clock.update(self.session(0, paused=True, item_id="movie-c"), 106)
        self.assertEqual(clock.item_id, "movie-c")
        self.assertTrue(clock.paused)
        self.assertEqual(clock.position(1000), 0)

    def test_changed_report_refreshes_twenty_second_extrapolation_window(self):
        clock = self.clock()
        clock.update(self.session(10), 100)
        clock.update(self.session(29), 119)
        self.assertEqual(clock.position(130), 40)
        self.assertEqual(clock.position(139), 49)
        self.assertIsNone(clock.position(139.001))

    def test_paused_seek_updates_frozen_position_in_both_directions(self):
        clock = self.clock()
        clock.update(self.session(10, paused=True), 100)
        clock.update(self.session(3, paused=True), 105)
        self.assertEqual(clock.position(500), 3)
        clock.update(self.session(30, paused=True), 500)
        self.assertEqual(clock.position(1000), 30)
        clock.update(self.session(30), 1000)
        self.assertEqual(clock.position(1001), 31)

    def test_external_session_mutation_does_not_retroactively_change_clock(self):
        clock = self.clock()
        session = self.session(10)
        clock.update(session, 100)
        session["PlayState"]["PositionTicks"] = 400_000_000
        self.assertEqual(clock.position(101), 11)
        clock.update(session, 101)
        self.assertEqual(clock.position(101), 40)

    def test_monotonic_timestamp_validation_survives_reset(self):
        clock = self.clock()
        clock.update(self.session(), 100)
        clock.update(None, 105)
        with self.assertRaises(ValueError):
            clock.update(self.session(), 104)
        self.assertIsNone(clock.position(105))


if __name__ == "__main__":
    unittest.main()
