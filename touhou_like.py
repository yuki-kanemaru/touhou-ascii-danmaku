#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
東方Project風 ASCII 弾幕シューティング（ターミナル版） - 段階1: 動く骨組み
標準ライブラリのみ / curses 不使用 / ANSI エスケープ描画 / msvcrt 入力
"""

from __future__ import annotations

import argparse
import math
import random
import sys
import time

FIELD_W = 40
FIELD_H = 18
TICK = 1.0 / 60.0

PLAYER_SYMBOL = "@"
BULLET_SYMBOL = "*"
ENEMY_SYMBOL = "o"
EMPTY_SYMBOL = "."
LIFE_SYMBOL = "\u25cf"
BOMB_SYMBOL = "\u2726"

PLAYER_SPEED = 24.0
PLAYER_RADIUS = 1.0
SHOT_INTERVAL = 0.09
SHOT_SPEED = 42.0
SHOT_DAMAGE = 1


def clamp(value, low, high):
    if value < low:
        return low
    if value > high:
        return high
    return value


def new_state(height=FIELD_H, width=FIELD_W, rng=None):
    """初期状態"""
    if rng is None:
        rng = random.Random(0xC0FFEE)
    return {
        "tick": 0,
        "time": 0.0,
        "width": width,
        "height": height,
        "rng": rng,
        "player": {
            "x": width / 2.0,
            "y": float(height - 2),
            "lives": 3,
            "invincible": 1.5,
            "shot_cd": 0.0,
            "focus": False,
        },
        "score": 0,
        "hi_score": 0,
        "graze": 0,
        "bullets": [],
        "player_shots": [],
        "enemies": [],
        "effects": [],
        "messages": [],
        "game_over": False,
        "elapsed": 0.0,
        "enemy_timer": 1.0,
    }


def update(state, dt, inputs=None):
    """state を dt 秒ぶん更新（I/O なし）"""
    if state["game_over"]:
        return state
    if dt < 0:
        dt = 0.0
    if dt > 0.25:
        dt = 0.25
    inp = inputs or {}
    move_x = clamp(float(inp.get("move_x", 0) or 0), -1.0, 1.0)
    move_y = clamp(float(inp.get("move_y", 0) or 0), -1.0, 1.0)
    shoot = bool(inp.get("shoot", False))

    state["tick"] += 1
    state["time"] += dt
    state["elapsed"] += dt

    _update_player(state, dt, move_x, move_y, shoot)
    _update_enemies(state, dt)
    _update_bullets(state, dt)
    _update_effects(state, dt)

    if state["player"]["lives"] < 0:
        state["player"]["lives"] = 0
        state["game_over"] = True
    return state


def _update_player(state, dt, move_x, move_y, shoot):
    p = state["player"]
    w, h = state["width"], state["height"]
    speed = PLAYER_SPEED * (0.55 if p["focus"] else 1.0)
    p["x"] = clamp(p["x"] + move_x * speed * dt, 1.0, w - 2.0)
    p["y"] = clamp(p["y"] + move_y * speed * dt, 1.0, h - 2.0)
    if p["invincible"] > 0:
        p["invincible"] = max(0.0, p["invincible"] - dt)
    p["shot_cd"] = max(0.0, p["shot_cd"] - dt)
    if shoot and p["shot_cd"] <= 0.0:
        p["shot_cd"] = SHOT_INTERVAL
        state["player_shots"].append([p["x"], p["y"] - 1.0, SHOT_SPEED, 0.0])


def _update_enemies(state, dt):
    state["enemy_timer"] -= dt
    if state["enemy_timer"] <= 0.0:
        state["enemy_timer"] = 1.4
        rng = state["rng"]
        state["enemies"].append({
            "x": rng.uniform(3.0, state["width"] - 4.0),
            "y": -1.0,
            "vy": rng.uniform(3.0, 6.0),
            "hp": 4 + int(rng.random() * 4),
            "fire_cd": rng.uniform(0.9, 1.8),
            "interval": rng.uniform(1.6, 2.6),
        })
    alive = []
    for e in state["enemies"]:
        e["y"] += e["vy"] * dt
        e["fire_cd"] -= dt
        if 0 <= e["y"] <= state["height"] + 2:
            alive.append(e)
    state["enemies"] = alive


def _update_bullets(state, dt):
    shots = state["player_shots"]
    remain = []
    for s in shots:
        s[1] -= s[2] * dt
        if s[1] < -1:
            continue
        hit = False
        for e in state["enemies"]:
            if math.hypot(s[0] - e["x"], s[1] - e["y"]) < 0.9:
                e["hp"] -= SHOT_DAMAGE
                hit = True
                break
        if not hit:
            remain.append(s)
    state["player_shots"] = remain

    alive = []
    for e in state["enemies"]:
        if e["hp"] <= 0:
            state["score"] += 30
            _add_effect(state, e["x"], e["y"])
        else:
            alive.append(e)
    state["enemies"] = alive

    remain = []
    for b in state["bullets"]:
        b[0] += b[2] * dt
        b[1] += b[3] * dt
        if -1 <= b[0] <= state["width"] and -1 <= b[1] <= state["height"] + 1:
            remain.append(b)
    state["bullets"] = remain


def _update_effects(state, dt):
    for e in state["effects"]:
        e[2] -= dt
    state["effects"] = [e for e in state["effects"] if e[2] > 0]


def _add_effect(state, x, y, life=0.25):
    if len(state["effects"]) < 64:
        state["effects"].append([float(x), float(y), life])


def _push_message(state, text):
    state["messages"].append(text)
    if len(state["messages"]) > 4:
        del state["messages"][0]


def render_rows(state):
    w, h = state["width"], state["height"]
    grid = [[EMPTY_SYMBOL] * w for _ in range(h)]

    def put(x, y, ch):
        ix, iy = int(round(x)), int(round(y))
        if 0 <= ix < w and 0 <= iy < h:
            grid[iy][ix] = ch

    for b in state["bullets"]:
        put(b[0], b[1], BULLET_SYMBOL)
    for e in state["enemies"]:
        put(e["x"], e["y"], ENEMY_SYMBOL)
    for e in state["effects"]:
        put(e[0], e[1], "%")
    p = state["player"]
    if p["invincible"] <= 0.0 or int(state["tick"] / 5) % 2 == 0:
        put(p["x"], p["y"], PLAYER_SYMBOL)
    return ["".join(row) for row in grid]


def render_header(state):
    p = state["player"]
    return "SCORE %06d  HI %06d  PLAYER %s  BOMB %s  GRAZE %d" % (
        state["score"], max(state["hi_score"], state["score"]),
        LIFE_SYMBOL * max(0, p["lives"]) if p["lives"] > 0 else "-",
        "-", state["graze"])


def render_screen(state, ansi=False, clear=True):
    lines = [render_header(state), render_rows(state)[0]]
    lines = [render_header(state)]
    lines.extend(render_rows(state))
    lines.append("-" * state["width"])
    lines.append("TICK %d  TIME %.1fs" % (state["tick"], state["elapsed"]))
    for msg in state["messages"]:
        lines.append(">> " + msg)
    text = "\n".join(lines)
    if ansi:
        return ("\x1b[2J\x1b[H" if clear else "\x1b[H") + text
    return text


def render_final_frame(state):
    return render_screen(state, ansi=False)


def scripted_inputs(frame, rng=None):
    phase = (frame // 45) % 4
    return {"move_x": 1 if phase in (0, 1) else -1, "move_y": 0, "shoot": True}


def run_demo(frames, seed=0xC0FFEE, dt=TICK):
    state = new_state(rng=random.Random(seed))
    total = max(0, int(frames))
    for frame in range(total):
        if not state["game_over"]:
            update(state, dt, scripted_inputs(frame))
        else:
            state["tick"] += 1
            state["time"] += dt
            state["elapsed"] += dt
    return state


class Keyboard:
    def __init__(self):
        try:
            import msvcrt  # type: ignore
            self.msvcrt = msvcrt
        except Exception:
            self.msvcrt = None

    @property
    def available(self):
        return self.msvcrt is not None and sys.stdin is not None and sys.stdin.isatty()

    def poll(self):
        if not self.available:
            return set()
        out = set()
        try:
            while self.msvcrt.kbhit():
                ch = self.msvcrt.getwch()
                if not ch:
                    break
                if ch in ("\x00", "\xe0"):
                    self.msvcrt.getwch()
                    continue
                out.add(ch.lower())
        except Exception:
            return set()
        return out


def key_to_input(keys, state):
    move_x = 0
    move_y = 0
    if "a" in keys:
        move_x -= 1
    if "d" in keys:
        move_x += 1
    if "w" in keys:
        move_y -= 1
    if "s" in keys:
        move_y += 1
    shoot = bool(keys & {"z", " ", "\r"})
    state["player"]["focus"] = "shift" in keys
    return {"move_x": move_x, "move_y": move_y, "shoot": shoot,
            "quit": bool(keys & {"q", "\x1b"})}


def _flush(text):
    sys.stdout.write(text)
    sys.stdout.flush()


def play():
    kb = Keyboard()
    if not kb.available:
        print("[注意] 端末入力が使えない環境のため、自動デモで動作確認します。")
        return run_main_demo(120)
    state = new_state()
    last = time.perf_counter()
    try:
        _flush("\x1b[?25l")
        while True:
            now = time.perf_counter()
            dt = now - last
            last = now
            if dt < TICK:
                time.sleep(max(0.0, TICK - dt))
            new_keys = kb.poll()
            inp = key_to_input(new_keys, state)
            if inp.get("quit"):
                break
            update(state, TICK, inp)
            _flush(render_screen(state, ansi=True))
            if state["game_over"]:
                break
    except KeyboardInterrupt:
        pass
    finally:
        _flush("\x1b[?25h\x1b[0m\n")
        print(render_final_frame(state))
        print("SCORE=%d LIVES=%d FRAMES=%d" % (
            state["score"], state["player"]["lives"], state["tick"]))
    return 0


def run_main_demo(frames):
    state = run_demo(frames)
    print(render_final_frame(state))
    print("SCORE=%d" % state["score"])
    print("LIVES=%d" % max(0, state["player"]["lives"]))
    print("FRAMES=%d" % state["tick"])
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="東方Project風 ASCII 弾幕シューティング")
    parser.add_argument("--demo", type=int, default=None, metavar="N")
    args = parser.parse_args(argv)
    if args.demo is not None:
        if args.demo < 0:
            print("--demo には 0 以上の整数を指定してください。", file=sys.stderr)
            return 2
        return run_main_demo(args.demo)
    return play()


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main())
