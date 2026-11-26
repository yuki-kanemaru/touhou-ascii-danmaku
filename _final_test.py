#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""touhou_like.py のコアロジックを I/O なしで検証する unittest（16件）。"""

import json
import os
import random
import tempfile
import unittest

import touhou_like as T


def make(**kw):
    return T.new_state(rng=random.Random(1234), **kw)


class TestPatterns(unittest.TestCase):
    def test_spread_fires_expected_count(self):
        s = make()
        n = T.fire_spread(s, 20, 5, 20, 17, count=9)
        self.assertEqual(n, 9)
        self.assertEqual(len(s["bullets"]), 9)

    def test_spiral_accumulates_and_rotates(self):
        s = make()
        T.fire_spiral(s, 20, 5, 0.0, arms=3)
        first = len(s["bullets"])
        phase = s["spiral_phase"]
        T.fire_spiral(s, 20, 5, phase, arms=3)
        self.assertEqual(first, 6)
        self.assertEqual(len(s["bullets"]), 12)
        self.assertNotEqual(phase, 0.0)

    def test_aimed_fires_toward_player(self):
        s = make()
        s["player"]["x"], s["player"]["y"] = 30.0, 16.0
        T.fire_aimed(s, 10.0, 4.0, 30.0, 16.0, shots=1)
        b = s["bullets"][0]
        # 自機方向（右斜め下）に飛ぶ
        self.assertGreater(b[2], 0)
        self.assertGreater(b[3], 0)

    def test_fire_pattern_dispatch_all_names(self):
        for pat in T.PATTERN_NAMES:
            s = make()
            self.assertGreater(T.fire_pattern(s, pat, 20, 5), 0, pat)

    def test_bullet_cap(self):
        s = make()
        for _ in range(T.MAX_BULLETS * 2):
            T.fire_aimed(s, 20, 3, 20, 17, shots=3)
        self.assertLessEqual(len(s["bullets"]), T.MAX_BULLETS)


class TestPlayer(unittest.TestCase):
    def test_move_and_clamp(self):
        s = make()
        T.update(s, 1.0, {"move_x": 1, "move_y": 0})
        self.assertLessEqual(s["player"]["x"], s["width"] - 1)
        T.update(s, 1.0, {"move_x": -1, "move_y": 0})
        self.assertGreaterEqual(s["player"]["x"], 1.0)

    def test_shoot_spawns_player_shot(self):
        s = make()
        T.update(s, T.TICK, {"shoot": True})
        self.assertEqual(len(s.get("player_shots", [])), 1)
        self.assertEqual(len(s["player_shots"]), 1)

    def test_dt_is_clamped(self):
        s = make()
        T.update(s, 10.0, {"move_x": 0})
        self.assertLessEqual(s["elapsed"], 0.25 + 1e-9)

    def test_hit_consumes_life_and_invincibility(self):
        s = make()
        p = s["player"]
        p["invincible"] = 0.0
        s["bullets"] = [[p["x"], p["y"], 0.0, 0.0]]
        T.update(s, T.TICK, {})
        self.assertEqual(p["lives"], 2)
        self.assertGreater(p["invincible"], 0.0)

    def test_invincible_ignores_bullet(self):
        s = make()
        p = s["player"]
        p["invincible"] = 5.0
        s["bullets"] = [[p["x"], p["y"], 0.0, 0.0]]
        T.update(s, T.TICK, {})
        self.assertEqual(p["lives"], 3)


class TestGraze(unittest.TestCase):
    def test_graze_ring_counts_and_scores(self):
        s = make()
        p = s["player"]
        p["invincible"] = 0.0
        # 当たり判定(1.0)より外、グレイズ(2.6)より内側に弾を置く
        s["bullets"] = [[p["x"] + 1.8, p["y"], 0.0, 0.0]]
        T.update(s, T.TICK, {})
        self.assertEqual(s["graze"], 1)
        self.assertEqual(p["lives"], 3)
        self.assertGreaterEqual(s["score"], 50)

    def test_far_bullet_does_not_graze(self):
        s = make()
        p = s["player"]
        p["invincible"] = 0.0
        s["bullets"] = [[p["x"] + 8.0, p["y"], 0.0, 0.0]]
        T.update(s, T.TICK, {})
        self.assertEqual(s["graze"], 0)


class TestBomb(unittest.TestCase):
    def test_bomb_clears_bullets_and_grants_invincibility(self):
        s = make()
        s["player"]["invincible"] = 0.0
        s["bullets"] = [[5.0, 5.0, 0.0, 0.0] for _ in range(10)]
        before = s["player"]["bombs"]
        T.update(s, T.TICK, {"bomb": True})
        self.assertEqual(s["player"]["bombs"], before - 1)
        self.assertEqual(len(s["bullets"]), 0)
        self.assertGreater(s["player"]["invincible"], 0.0)
        self.assertGreater(s["score"], 0)

    def test_bomb_cannot_go_negative(self):
        s = make()
        s["player"]["bombs"] = 0
        T.update(s, T.TICK, {"bomb": True})
        self.assertEqual(s["player"]["bombs"], 0)


class TestBoss(unittest.TestCase):
    def test_boss_spawns_after_interval(self):
        s = make()
        self.assertIsNone(s["boss"])
        s["next_boss_timer"] = 0.1
        for _ in range(int(0.5 / T.TICK)):
            T.update(s, T.TICK, {})
        self.assertIsNotNone(s["boss"])

    def test_boss_has_spell_card_and_hp_bar(self):
        s = make()
        T.spawn_boss(s)
        boss = s["boss"]
        self.assertGreater(boss["hp"], 0)
        self.assertTrue(boss["name"].startswith("\u7b26"))  # 符「…」
        line = T.render_boss_line(s)
        self.assertIn("HP", line)
        self.assertIn("[", line)

    def test_boss_defeat_adds_score(self):
        s = make()
        T.spawn_boss(s)
        s["boss"]["hp"] = 0
        s["score"] = 0
        T.update(s, T.TICK, {})
        self.assertIsNone(s["boss"])
        self.assertGreater(s["score"], 0)


class TestRender(unittest.TestCase):
    def test_render_rows_shape_and_symbols(self):
        s = make()
        s["player"]["invincible"] = 0.0
        rows = T.render_rows(s)
        self.assertEqual(len(rows), T.FIELD_H)
        for r in rows:
            self.assertEqual(len(r), T.FIELD_W)
        flat = "".join(rows)
        self.assertIn(T.PLAYER_SYMBOL, flat)
        self.assertNotIn("\x1b", flat)  # 素のテキスト

    def test_render_header_and_screen(self):
        s = make()
        s["score"] = 123
        s["hi_score"] = 5000
        header = T.render_header(s)
        self.assertIn("SCORE 000123", header)
        self.assertIn("HI 005000", header)
        screen = T.render_final_frame(s)
        self.assertIn(T.PLAYER_SYMBOL, screen)
        self.assertGreaterEqual(len(screen.splitlines()), T.FIELD_H + 3)


class TestHighscoreAndDemo(unittest.TestCase):
    def test_highscore_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "highscore.json")
            self.assertEqual(T.load_highscore(path), 0)  # 無ければ 0
            self.assertTrue(T.save_highscore(777, path))
            self.assertEqual(T.load_highscore(path), 777)
            # 低いスコアでは上書きしない
            T.save_highscore(10, path)
            self.assertEqual(T.load_highscore(path), 777)

    def test_highscore_broken_file_returns_zero(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "highscore.json")
            with open(path, "w", encoding="utf-8") as fp:
                fp.write("{ this is not json")
            self.assertEqual(T.load_highscore(path), 0)
            self.assertTrue(T.save_highscore(50, path))
            self.assertEqual(T.load_highscore(path), 50)
            with open(path, encoding="utf-8") as fp:
                self.assertEqual(json.load(fp)["highscore"], 50)

    def test_demo_runs_fixed_frames_without_io(self):
        s = T.run_demo(300)
        self.assertEqual(s["tick"], 300)
        self.assertGreaterEqual(s["score"], 0)
        self.assertEqual(len(T.render_final_frame(s).splitlines()) >= T.FIELD_H, True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
