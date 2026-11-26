#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
東方Project風 ASCII 弾幕シューティング（ターミナル版）
=====================================================

* 標準ライブラリのみ（curses 不使用 / ANSI エスケープで描画）
* 入力: pynput（任意）> msvcrt（Windows）> termios（Linux/macOS）の順に自動選択
* ロジック（state 辞書 + 純粋な更新関数）と入出力（ANSI 描画 / 端末入力）を分離
* `--demo N` でキーボード無しの自動プレイ → 最終フレームを素のテキストで出力

実行例:
    python touhou_like.py            … 通常プレイ（WASD / Shift・Z ショット / X ボム / Q 終了）
    python touhou_like.py --demo 300 … 300フレーム自動実行し最終フレームと成績を表示
    python touhou_like.py --list     … 弾幕パターン一覧（テスト用の小さな自己診断）

記号:
    @ 自機   B ボス   o 雑魚敵   * 弾   + かすった弾   . 何もない
    画面下の行が自機の初期位置。自機の当たり判定は「・」1文字分の点だけ。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import time

# ---------------------------------------------------------------------------
# 定数（盤面・チューニング）
# ---------------------------------------------------------------------------
FIELD_W = 40          # 盤面の横幅（文字数）
FIELD_H = 18          # 盤面の縦幅（行数）
TICK = 1.0 / 60.0     # 60FPS 相当
MAX_BULLETS = 900     # 弾の上限（描画負荷とメモリの保護）
MAX_STEPS_PER_FRAME = 5

PLAYER_SYMBOL = "@"
BOMB_SYMBOL = "\u2726"    # ✦
LIFE_SYMBOL = "\u25cf"    # ●
BULLET_SYMBOL = "*"
GRAZE_SYMBOL = "+"
ENEMY_SYMBOL = "o"
BOSS_SYMBOL = "B"
EMPTY_SYMBOL = "."

# 自機
PLAYER_RADIUS = 1.0        # 当たり判定（とても小さい）
GRAZE_RADIUS = 2.6         # グレイズ判定（当たり判定の外側リング）
PLAYER_SPEED = 24.0        # 文字/秒
SHOT_INTERVAL = 0.09       # ショット間隔（秒）
SHOT_SPEED = 42.0
SHOT_DAMAGE = 1

# ボム
BOMB_INVINCIBLE = 1.8      # 秒
BOMB_START = 2

# ボス
BOSS_INTERVAL = 12.0       # ボス出現間隔（秒）
BOSS_HP_BASE = 90

# スコア
SCORE_SETUP_TIME = 25.0

PATTERN_NAMES = ("spread", "spiral", "aimed")

HIGHSCORE_FILE = "highscore.json"


# ---------------------------------------------------------------------------
# ユーティリティ
# ---------------------------------------------------------------------------
def clamp(value, low, high):
    """value を [low, high] に丸める。"""
    if value < low:
        return low
    if value > high:
        return high
    return value


def _dist(ax, ay, bx, by):
    return math.hypot(ax - bx, ay - by)


# ---------------------------------------------------------------------------
# 弾幕パターン生成（純粋関数: state を書き換えるだけで I/O しない）
# ---------------------------------------------------------------------------
def fire_spread(state, x, y, tx, ty, speed=15.0, count=9, arc=math.pi * 0.9):
    """扇状弾幕: 自機の方向を中心に扇状にばらまく。"""
    base = math.atan2(ty - y, tx - x)
    if count <= 1:
        angles = [base]
    else:
        angles = [base - arc / 2 + arc * i / (count - 1) for i in range(count)]
    for ang in angles:
        _spawn_bullet(state, x, y, ang, speed)
    return count


def fire_spiral(state, x, y, phase, speed=13.0, arms=3, twist=0.55):
    """螺旋弾幕: 位相を回しながら腕を伸ばす。呼ぶたびに少し回転する。"""
    for i in range(arms):
        ang = phase + i * (2.0 * math.pi / arms)
        _spawn_bullet(state, x, y, ang, speed)
        _spawn_bullet(state, x, y, ang + twist, speed * 0.78)
    state["spiral_phase"] = phase + 0.38
    return arms * 2


def fire_aimed(state, x, y, tx, ty, shots=3, speed=18.0, spread=0.14):
    """自機狙い弾: 自機の現在位置へ直線的に撃ち込む。"""
    base = math.atan2(ty - y, tx - x)
    total = 0
    for i in range(shots):
        offset = 0.0 if shots == 1 else -spread + (2 * spread) * i / (shots - 1)
        _spawn_bullet(state, x, y, base + offset, speed)
        total += 1
    return total


def _spawn_bullet(state, x, y, angle, speed):
    bullets = state["bullets"]
    if len(bullets) >= MAX_BULLETS:
        return
    bullets.append([float(x), float(y), math.cos(angle) * speed, math.sin(angle) * speed])


def fire_pattern(state, pattern, x, y):
    """名前から弾幕パターンを選択して発射する。"""
    px, py = state["player"]["x"], state["player"]["y"]
    if pattern == "spread":
        return fire_spread(state, x, y, px, py)
    if pattern == "spiral":
        return fire_spiral(state, x, y, state.get("spiral_phase", 0.0))
    if pattern == "aimed":
        return fire_aimed(state, x, y, px, py)
    return 0


# ---------------------------------------------------------------------------
# 状態の生成
# ---------------------------------------------------------------------------
def new_state(height=FIELD_H, width=FIELD_W, rng=None):
    """初期状態を辞書で返す（この関数以外にグローバルな状態を持たない）。"""
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
            "bombs": BOMB_START,
            "invincible": 1.5,
            "shot_cd": 0.0,
            "focus": False,
        },
        "score": 0,
        "hi_score": 0,
        "graze": 0,
        "bullets": [],
        "enemies": [],
        "boss": None,
        "next_boss_timer": BOSS_INTERVAL,
        "boss_index": 0,
        "spiral_phase": 0.0,
        "enemy_timer": 1.0,
        "effects": [],       # [[x, y, 残り時間]]
        "messages": [],
        "bomb_timer": 0.0,
        "game_over": False,
        "elapsed": 0.0,
    }


def spell_card_name(index):
    """ボスごとのスペルカード名。"""
    names = (
        "\u7b26\u300c\u87ba\u65cb\u5f3e\u5e55\u300d",          # 符「螺旋弾幕」
        "\u5f3e\u5e55\u300c\u5c04\u7684\u98db\u7ffc\u300d",      # 弾幕「射的飛翼」
        "\u4e21\u5100\u300c\u65b9\u5411\u97f3\u6ce2\u300d",      # 両儀「方向音波」
    )
    return names[index % len(names)]


BOSS_KIND = (
    (["spread", "aimed"], 8),
    (["aimed", "spiral"], 10),
    (["spiral", "spread", "aimed"], 6),
)
BOSS_PATTERN = ("spread", "aimed", "spiral")


# ---------------------------------------------------------------------------
# 更新（純粋ロジック: 入力は dict、出力は辞書の書き換えのみ）
# ---------------------------------------------------------------------------
def update(state, dt, inputs=None):
    """state を dt 秒ぶん更新する。inputs は {"move_x": -1..1, "move_y":..., "shoot": bool, "bomb": bool}。

    戻り値は state そのもの（テストしやすいように同じオブジェクトを返す）。
    """
    if state["game_over"]:
        return state
    if dt < 0:
        dt = 0.0
    if dt > 0.25:
        dt = 0.25  # タブ切り替え等による巨大な dt を丸める

    inp = inputs or {}
    move_x = clamp(float(inp.get("move_x", 0) or 0), -1.0, 1.0)
    move_y = clamp(float(inp.get("move_y", 0) or 0), -1.0, 1.0)
    shoot = bool(inp.get("shoot", False))
    bomb = bool(inp.get("bomb", False))

    state["tick"] += 1
    state["time"] += dt
    state["elapsed"] += dt

    _update_player(state, dt, move_x, move_y, shoot, bomb)
    _update_boss_spawn(state, dt)
    _update_enemies(state, dt)
    _update_bullets(state, dt)
    _update_effects(state, dt)

    if state["player"]["lives"] < 0:
        state["player"]["lives"] = 0
        state["game_over"] = True
    return state


def _update_player(state, dt, move_x, move_y, shoot, bomb):
    p = state["player"]
    w, h = state["width"], state["height"]

    if bomb and p["bombs"] > 0 and p["invincible"] <= 0.0:
        p["bombs"] -= 1
        p["invincible"] = BOMB_INVINCIBLE
        removed = len(state["bullets"])
        state["bullets"].clear()
        state["bomb_timer"] = 0.45
        state["score"] += 10 * removed
        _push_message(state, "BOMB! 弾幕消去 %d" % removed)

    speed = PLAYER_SPEED * (0.55 if p["focus"] else 1.0)
    p["x"] = clamp(p["x"] + move_x * speed * dt, 1.0, w - 2.0)
    p["y"] = clamp(p["y"] + move_y * speed * dt, 1.0, h - 2.0)

    if p["invincible"] > 0:
        p["invincible"] = max(0.0, p["invincible"] - dt)

    p["shot_cd"] = max(0.0, p["shot_cd"] - dt)
    if shoot and p["shot_cd"] <= 0.0:
        p["shot_cd"] = SHOT_INTERVAL
        state["shots"] = state.get("shots", 0) + 1
        state.setdefault("player_shots", []).append(
            [p["x"], p["y"] - 1.0, SHOT_SPEED, 0.0]
        )


def _update_boss_spawn(state, dt):
    if state["boss"] is not None:
        return
    state["next_boss_timer"] -= dt
    if state["next_boss_timer"] <= 0.0:
        spawn_boss(state)


def spawn_boss(state):
    """ボスを1体出現させる（テストからも直接呼べる）。"""
    idx = state["boss_index"]
    patterns, interval = BOSS_KIND[idx % len(BOSS_KIND)]
    max_hp = BOSS_HP_BASE + 40 * idx
    state["boss"] = {
        "x": state["width"] / 2.0,
        "y": 3.0,
        "hp": max_hp,
        "max_hp": max_hp,
        "patterns": list(patterns),
        "pattern": patterns[0],
        "pattern_index": 0,
        "fire_cd": 0.8,
        "interval": interval / 60.0,
        "name": spell_card_name(idx),
        "spiral_phase": 0.0,
        "dir": 1.0,
    }
    state["boss_index"] = idx + 1
    state["next_boss_timer"] = BOSS_INTERVAL
    _push_message(state, "BOSS: %s" % state["boss"]["name"])
    return state["boss"]


def _update_enemies(state, dt):
    w = state["width"]
    h = state["height"]

    # 雑魚敵の定期出現
    state["enemy_timer"] -= dt
    if state["enemy_timer"] <= 0.0:
        state["enemy_timer"] = 1.15
        _spawn_enemy(state)

    # 雑魚敵の移動と発射
    alive = []
    for e in state["enemies"]:
        e["y"] += e["vy"] * dt
        if e["dir"] != 0:
            e["x"] += e["vx"] * dt
            if e["x"] < 2.0 or e["x"] > w - 3.0:
                e["dir"] *= -1.0
        e["fire_cd"] -= dt
        if e["fire_cd"] <= 0.0 and e["y"] > 0:
            e["fire_cd"] = e["interval"]
            fire_pattern(state, e["pattern"], e["x"], e["y"])
        if e["y"] <= h + 2 and e["y"] >= -4:
            alive.append(e)
    state["enemies"] = alive

    # ボスの挙動
    boss = state["boss"]
    if boss is not None:
        boss["x"] += boss["dir"] * 9.0 * dt
        if boss["x"] < 5.0 or boss["x"] > w - 6.0:
            boss["dir"] *= -1.0
            boss["x"] = clamp(boss["x"], 5.0, w - 6.0)
        boss["spiral_phase"] += 1.4 * dt
        boss["fire_cd"] -= dt
        if boss["fire_cd"] <= 0.0:
            boss["fire_cd"] = boss["interval"]
            pat = boss["patterns"][boss["pattern_index"] % len(boss["patterns"])]
            boss["pattern"] = pat
            boss["pattern_index"] += 1
            state["spiral_phase"] = boss["spiral_phase"] * 3.0 + boss["pattern_index"]
            fire_pattern(state, pat, boss["x"], boss["y"])
        if boss["hp"] <= 0:
            bonus = 500 + 250 * state["boss_index"]
            state["score"] += bonus
            _push_message(state, "BOSS DEFEATED! +%d" % bonus)
            _add_effect(state, boss["x"], boss["y"], life=0.4)
            state["boss"] = None
            state["next_boss_timer"] = BOSS_INTERVAL


def _spawn_enemy(state):
    rng = state["rng"]
    pattern = rng.choice(PATTERN_NAMES)
    x = rng.uniform(3.0, state["width"] - 4.0)
    drift = 1 if rng.random() < 0.5 else -1
    state["enemies"].append(
        {
            "x": x,
            "y": -1.0,
            "vx": drift * rng.uniform(2.0, 6.0),
            "vy": rng.uniform(3.0, 6.5),
            "dir": drift,
            "hp": 4 + int(rng.random() * 4),
            "pattern": pattern,
            "fire_cd": rng.uniform(0.9, 1.8),
            "interval": rng.uniform(1.6, 2.6),
        }
    )


def _update_bullets(state, dt):
    w = state["width"]
    h = state["height"]
    p = state["player"]
    px, py = p["x"], p["y"]

    # 自機弾（真上に飛ぶ）
    shots = state.get("player_shots", [])
    remain_shots = []
    for s in shots:
        s[1] -= s[2] * dt
        if s[1] < -1:
            continue
        hit = False
        for e in state["enemies"]:
            if _dist(s[0], s[1], e["x"], e["y"]) < 0.9:
                e["hp"] -= SHOT_DAMAGE
                hit = True
                break
        if not hit and state["boss"] is not None:
            b = state["boss"]
            if _dist(s[0], s[1], b["x"], b["y"]) < 1.4:
                b["hp"] -= SHOT_DAMAGE
                state["score"] += 1
                hit = True
        if not hit:
            remain_shots.append(s)
    state["player_shots"] = remain_shots

    # 雑魚敵の処理（自機弾で倒れたもの）
    alive = []
    for e in state["enemies"]:
        if e["hp"] <= 0:
            state["score"] += 30
            _add_effect(state, e["x"], e["y"])
        else:
            alive.append(e)
    state["enemies"] = alive

    # 敵弾の移動 + かすり + 被弾
    remain = []
    grazed_now = 0
    hit_now = False
    for b in state["bullets"]:
        b[0] += b[2] * dt
        b[1] += b[3] * dt
        if b[0] < -1 or b[0] > w or b[1] < -1 or b[1] > h + 1:
            continue
        d = _dist(b[0], b[1], px, py)
        if d <= PLAYER_RADIUS:
            if p["invincible"] <= 0.0:
                hit_now = True
            continue
        if d <= GRAZE_RADIUS:
            grazed_now += 1
        remain.append(b)
    state["bullets"] = remain

    if grazed_now:
        state["graze"] += grazed_now
        state["score"] += 50 * grazed_now

    if hit_now:
        _player_hit(state)


def _player_hit(state):
    p = state["player"]
    p["lives"] -= 1
    p["invincible"] = 2.0
    state["bullets"] = [b for b in state["bullets"] if _dist(b[0], b[1], p["x"], p["y"]) > 3.5]
    _add_effect(state, p["x"], p["y"])
    _push_message(state, "MISS! 残機 %d" % max(0, p["lives"]))
    p["x"] = state["width"] / 2.0
    p["y"] = float(state["height"] - 2)


def _update_effects(state, dt):
    for e in state["effects"]:
        e[2] -= dt
    state["effects"] = [e for e in state["effects"] if e[2] > 0]
    if state["bomb_timer"] > 0:
        state["bomb_timer"] = max(0.0, state["bomb_timer"] - dt)


def _add_effect(state, x, y, life=0.25):
    if len(state["effects"]) < 64:
        state["effects"].append([float(x), float(y), life])


def _push_message(state, text):
    state["messages"].append(text)
    if len(state["messages"]) > 4:
        del state["messages"][0]


# ---------------------------------------------------------------------------
# 描画（純粋関数: state -> 文字列リスト。I/O しない）
# ---------------------------------------------------------------------------
def render_rows(state):
    """盤面を文字列のリストで返す（テスト・デモ・ANSI描画の共通経路）。"""
    w = state["width"]
    h = state["height"]
    grid = [[EMPTY_SYMBOL] * w for _ in range(h)]

    def put(x, y, ch):
        ix = int(round(x))
        iy = int(round(y))
        if 0 <= ix < w and 0 <= iy < h:
            grid[iy][ix] = ch

    for b in state["bullets"]:
        put(b[0], b[1], BULLET_SYMBOL)
    for e in state["enemies"]:
        put(e["x"], e["y"], ENEMY_SYMBOL)
    boss = state["boss"]
    if boss is not None:
        put(boss["x"], boss["y"], BOSS_SYMBOL)
    for e in state["effects"]:
        put(e[0], e[1], "%")

    p = state["player"]
    if p["invincible"] <= 0.0 or int(state["tick"] / 5) % 2 == 0:
        put(p["x"], p["y"], PLAYER_SYMBOL)

    return ["".join(row) for row in grid]


def render_header(state):
    p = state["player"]
    return "SCORE %06d  HI %06d  PLAYER %s  BOMB %s  GRAZE %d" % (
        state["score"],
        max(state["hi_score"], state["score"]),
        LIFE_SYMBOL * max(0, p["lives"]) if p["lives"] > 0 else "-",
        BOMB_SYMBOL * max(0, p["bombs"]) if p["bombs"] > 0 else "-",
        state["graze"],
    )


def render_boss_line(state):
    boss = state["boss"]
    if boss is None:
        remain = max(0.0, state["next_boss_timer"])
        return "---------- BOSS IN %4.1fs ----------" % remain
    ratio = max(0.0, boss["hp"]) / float(boss["max_hp"])
    filled = int(round(ratio * 10))
    bar = "\u2588" * filled + "-" * (10 - filled)
    return "---------- BOSS: %s  HP [%s]  %s" % (
        "\u5f3e\u5e55\u5996\u7cbe",
        bar,
        boss["name"],
    )


def render_screen(state, ansi=False, clear=True):
    """画面全体を1つの文字列で返す。ansi=True で ANSI 制御付き（本番描画用）。"""
    lines = [render_header(state), render_boss_line(state)]
    lines.extend(render_rows(state))
    lines.append("-" * state["width"])
    status = "TICK %d  TIME %.1fs  %s" % (
        state["tick"],
        state["elapsed"],
        "GAME OVER (Q)" if state["game_over"] else ("BOMBING" if state["bomb_timer"] > 0 else "PLAYING"),
    )
    lines.append(status)
    for msg in state["messages"]:
        lines.append(">> " + msg)
    text = "\n".join(lines)
    if ansi:
        prefix = "\x1b[2J\x1b[H" if clear else "\x1b[H"
        return prefix + text
    return text


def render_final_frame(state):
    """デモ用: ANSI を含まない素の最終フレーム。"""
    return render_screen(state, ansi=False)


# ---------------------------------------------------------------------------
# ハイスコア（例外に強い読み書き）
# ---------------------------------------------------------------------------
def load_highscore(path=HIGHSCORE_FILE):
    """highscore.json から読み込む。壊れていても例外を投げずに 0 を返す。"""
    try:
        with open(path, "r", encoding="utf-8") as fp:
            data = json.load(fp)
        if isinstance(data, dict):
            value = data.get("highscore", 0)
        else:
            value = data
        return max(0, int(value))
    except Exception:
        return 0


def save_highscore(score, path=HIGHSCORE_FILE):
    """highscore.json に保存。失敗しても例外を投げず False を返す。"""
    try:
        score = int(score)
        if score < 0:
            score = 0
        old = load_highscore(path)
        if score < old:
            score = old
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fp:
            json.dump({"highscore": score}, fp)
        os.replace(tmp, path)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# スクリプト入力（デモ / テスト用。乱数は固定シードで再現可能）
# ---------------------------------------------------------------------------
def scripted_inputs(frame, rng=None):
    """フレーム番号から決定論的な入力を返す（左右移動 + ショット + ボム）。"""
    if rng is None:
        rng = random.Random(frame * 7919 + 13)
    phase = (frame // 45) % 4
    move_x = 1 if phase in (0, 1) else -1
    move_y = 0
    if frame % 180 in (60, 61, 62):
        move_y = -1
    elif frame % 180 in (120, 121):
        move_y = 1
    shoot = True
    bomb = frame % 240 == 150 or frame % 240 == 151
    return {
        "move_x": move_x + (rng.choice((-1, 0, 1)) * 0.0),
        "move_y": move_y,
        "shoot": shoot,
        "bomb": bomb,
    }


def run_demo(frames, seed=0xC0FFEE, dt=TICK):
    """demo モードの本体。描画せず state を更新し、最終 state を返す。"""
    state = new_state(rng=random.Random(seed))
    total = max(0, int(frames))
    for frame in range(total):
        if not state["game_over"]:
            update(state, dt, scripted_inputs(frame))
        else:
            # ゲームオーバー後もフレームは進める（FRAMES が指定数と一致し、
            # 最終フレームが「そこで止まった盤面」になるようにするため）
            state["tick"] += 1
            state["time"] += dt
            state["elapsed"] += dt
    return state


# ---------------------------------------------------------------------------
# リアルタイム入力
#   1) pynput（任意依存・推奨） … 押下/離上を取得できるので「押しっぱなし」が快適
#   2) Windows: msvcrt
#   3) Linux/macOS: termios + tty + select
# ---------------------------------------------------------------------------
def map_pynput_key(key):
    """pynput のキーを本ゲームのキー名へ変換する（未知は None）。

    例: KeyCode(char='a') -> 'a' / Key.shift -> 'shift' / Key.left -> 'left'
    """
    name = getattr(key, "name", None)
    if name:
        table = {
            "shift": "shift", "shift_l": "shift", "shift_r": "shift",
            "left": "left", "right": "right", "up": "up", "down": "down",
            "space": " ", "enter": "\r", "esc": "\x1b",
        }
        return table.get(name)
    char = getattr(key, "char", None)
    if isinstance(char, str) and char:
        return char.lower()
    return None


# ---------------------------------------------------------------------------
# Kitty keyboard protocol（kitty / wezterm / foot / ghostty 等）
#   キーの押下・リピート・離上を CSI ... u で受け取れる
# ---------------------------------------------------------------------------
KITTY_KEYS = {
    57350: "left", 57351: "right", 57352: "up", 57353: "down",
    57441: "shift", 57447: "shift",  # left_shift / right_shift
}
KITTY_FLAGS = 1 | 2 | 8  # 1:曖昧さ解消 / 2:イベント種別 / 8:全キーをCSI uで


def kitty_key_name(code):
    """kitty のキーコードをゲーム内キー名へ変換する（未知は None）。"""
    if code is None:
        return None
    if code in KITTY_KEYS:
        return KITTY_KEYS[code]
    if code == 32:  # space
        return " "
    if 32 <= code < 57344:
        ch = chr(code)
        if ch.isprintable() and not ch.isspace():
            return ch.lower()
    return None


def parse_kitty_buffer(buffer):
    """CSI ... u 形式の入力を解析する。

    戻り値: (pressed, released, rest)
      pressed / released はキー名の集合、rest は未解析の残りバイト列。
    形式: ESC [ code:alt:base ; mods:event ; text u
    """
    pressed, released = set(), set()
    text = buffer.decode("latin-1", "ignore")
    while True:
        start = text.find("\x1b[")
        if start < 0:
            text = ""
            break
        end = text.find("u", start)
        if end < 0:
            text = text[start:]
            break
        body = text[start + 2:end]
        text = text[end + 1:]
        if not body or not body[0].isdigit():
            continue
        fields = body.split(";")
        try:
            code = int(fields[0].split(":")[0])
        except (ValueError, IndexError):
            continue
        event = 1
        if len(fields) > 1 and ":" in fields[1]:
            ev = fields[1].split(":")[-1]
            if ev.isdigit():
                event = int(ev)
        name = kitty_key_name(code)
        if not name:
            continue
        if event == 3:
            released.add(name)
        else:
            pressed.add(name)
    if len(text) > 8192:
        text = text[-8192:]
    return pressed, released, text.encode("latin-1", "ignore")


class Keyboard:
    """非ブロッキング入力。(active, edges) を返す。

    active: いま押されているキー（移動・ショット・低速移動に使う）
    edges : 前回 poll 以降に新しく押されたキー（ボム・終了など1回だけの入力に使う）
    """

    ESC_SEQ = {
        b"[A": "up", b"[B": "down", b"[C": "right", b"[D": "left",
        b"OA": "up", b"OB": "down", b"OC": "right", b"OD": "left",
    }

    HOLD_GRACE = 0.16  # 秒: 端末でキーリピート待ちの間も「押されている」とみなす猶予

    def __init__(self, prefer_pynput=True, force=None):
        force = force or "auto"
        self.backend = None
        self.msvcrt = None
        self._fd = None
        self._saved = None
        self._termios = None
        self._select = None
        self._listener = None
        self._lock = None
        self._held = set()
        self._edges = set()
        self._hold_until = {}
        self._kitty_buf = b""
        self._win_console = None

        # 0) kitty keyboard protocol（Waylandでも押下/離上が取れる）
        if force in ("auto", "kitty") and self._init_kitty():
            return

        # 1) pynput（押しっぱなし・斜め移動・Shift が快適に取れる）
        if force in ("auto", "pynput") and prefer_pynput:
            if self._init_pynput():
                return

        # 2) Windows: msvcrt
        if force in ("auto", "msvcrt"):
            try:
                import msvcrt  # type: ignore

                self.msvcrt = msvcrt
                self.backend = "msvcrt"
                return
            except Exception:
                self.msvcrt = None

        # 3) Linux / macOS: termios + select
        if force in ("auto", "termios") and sys.stdin is not None:
            try:
                import select
                import termios
                import tty

                if sys.stdin.isatty():
                    self._fd = sys.stdin.fileno()
                    self._termios = termios
                    self._select = select
                    self._saved = termios.tcgetattr(self._fd)
                    tty.setcbreak(self._fd)
                    self.backend = "posix"
            except Exception:
                self.backend = None

    def _init_pynput(self):
        try:
            from pynput import keyboard as _pk
        except Exception:
            return False
        try:
            import threading

            self._lock = threading.Lock()

            def on_press(key):
                mapped = map_pynput_key(key)
                if not mapped:
                    return
                with self._lock:
                    if mapped not in self._held:
                        self._edges.add(mapped)
                    self._held.add(mapped)

            def on_release(key):
                mapped = map_pynput_key(key)
                if not mapped:
                    return
                with self._lock:
                    self._held.discard(mapped)

            listener = _pk.Listener(on_press=on_press, on_release=on_release)
            listener.daemon = True
            listener.start()
            self._listener = listener
            self.backend = "pynput"
            self._setup_console_no_echo()
            return True
        except Exception:
            self._listener = None
            return False

    def _setup_console_no_echo(self):
        """pynput 使用中は端末から読まないため、キーのエコーと行入力を止める。"""
        # Windows: コンソールモードを直接変更する
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetStdHandle(-10)  # STD_INPUT_HANDLE
            mode = ctypes.c_uint32()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)) and mode.value:
                echo_input = 0x0004
                line_input = 0x0002
                self._win_console = (kernel32, handle, mode.value)
                kernel32.SetConsoleMode(
                    handle, mode.value & ~echo_input & ~line_input
                )
                return True
        except Exception:
            self._win_console = None

        # Linux / macOS: cbreak（エコー無効・1文字入力）
        try:
            import termios
            import tty

            if sys.stdin is not None and sys.stdin.isatty():
                self._fd = sys.stdin.fileno()
                self._termios = termios
                self._saved = termios.tcgetattr(self._fd)
                tty.setcbreak(self._fd)
                return True
        except Exception:
            pass
        return False

    @property
    def available(self):
        if self.backend in ("pynput", "posix"):
            return True
        if self.backend == "msvcrt":
            return sys.stdin is not None and sys.stdin.isatty()
        return False

    @property
    def platform_name(self):
        return {
            "kitty": "kitty keyboard protocol（押下/離上対応）",
            "pynput": "pynput（押しっぱなし対応）",
            "msvcrt": "Windows (msvcrt)",
            "posix": "Linux/macOS (termios)",
            None: "未対応",
        }[self.backend]

    def poll(self):
        """(active, edges) を返す。"""
        if self.backend == "pynput":
            with self._lock:
                active = set(self._held)
                edges = set(self._edges)
                self._edges.clear()
            return active, edges
        if self.backend == "kitty":
            return self._poll_kitty()
        if self.backend == "msvcrt":
            return self._with_hold_grace(self._poll_windows())
        if self.backend == "posix":
            return self._with_hold_grace(self._poll_posix())
        return set(), set()

    def _with_hold_grace(self, keys):
        """キーリピート間を補間する（pynput が無い環境の操作感を改善）。

        端末からは「押した瞬間」しか届かないため、最後に入力を見てから
        HOLD_GRACE 秒は押され続けているとみなす。
        """
        now = time.perf_counter()
        for k in keys:
            self._hold_until[k] = now + self.HOLD_GRACE
        active = {k for k, until in self._hold_until.items() if until > now}
        if len(self._hold_until) > 16:
            self._hold_until = {k: u for k, u in self._hold_until.items() if u > now}
        return active, set(keys)

    # ------------------------------------------------------------------
    # kitty keyboard protocol
    # ------------------------------------------------------------------
    @staticmethod
    def kitty_capable():
        """kitty 系の端末か（環境変数で判定）。"""
        term = os.environ.get("TERM", "")
        return bool(os.environ.get("KITTY_WINDOW_ID")) or "kitty" in term

    def _write_kb(self, text):
        try:
            sys.stdout.write(text)
            sys.stdout.flush()
        except Exception:
            pass

    def _init_kitty(self):
        if not self.kitty_capable():
            return False
        if sys.stdin is None or not sys.stdin.isatty():
            return False
        try:
            import select
            import termios
            import tty

            self._fd = sys.stdin.fileno()
            self._termios = termios
            self._select = select
            self._saved = termios.tcgetattr(self._fd)
            tty.setcbreak(self._fd)
            self._kitty_buf = b""
            self._held = set()
            self._write_kb("\x1b[>%du" % KITTY_FLAGS)  # 押下/離上の報告を有効化
            self.backend = "kitty"
            return True
        except Exception:
            return False

    def _poll_kitty(self):
        data = b""
        while True:
            r, _, _ = self._select.select([self._fd], [], [], 0)
            if not r:
                break
            try:
                chunk = os.read(self._fd, 256)
            except OSError:
                break
            if not chunk:
                break
            data += chunk
        if data:
            self._kitty_buf += data
        pressed, released, rest = parse_kitty_buffer(self._kitty_buf)
        self._kitty_buf = rest

        before = set(self._held)
        self._held |= pressed
        self._held -= released
        edges = pressed - before
        return set(self._held), edges

    def _poll_windows(self):
        out = set()
        try:
            while self.msvcrt.kbhit():
                ch = self.msvcrt.getwch()
                if not ch:
                    break
                if ch in ("\x00", "\xe0"):  # 拡張キー(矢印等)の2バイト目
                    code = self.msvcrt.getwch()
                    arrow = {"H": "up", "P": "down", "K": "left", "M": "right"}.get(
                        (code or "").upper()
                    )
                    if arrow:
                        out.add(arrow)
                    continue
                out.add(ch.lower())
        except Exception:
            return set()
        return out

    def _read_byte(self, timeout=0.0):
        r, _, _ = self._select.select([self._fd], [], [], timeout)
        if not r:
            return b""
        try:
            return os.read(self._fd, 1)
        except OSError:
            return b""

    def _poll_posix(self):
        out = set()
        while True:
            ch = self._read_byte(0.0)
            if not ch:
                break
            if ch == b"\x1b":
                # ESC 単独か、矢印キーのエスケープシーケンスかを判別する
                nxt = self._read_byte(0.005)
                if nxt in (b"[", b"O"):
                    third = self._read_byte(0.005)
                    key = self.ESC_SEQ.get(nxt + third)
                    if key:
                        out.add(key)
                        continue
                out.add("\x1b")
                continue
            if ch in (b"\r", b"\n"):
                out.add("\r")
                continue
            out.add(ch.decode("utf-8", "ignore").lower())
        return out

    def close(self):
        """リスナー停止と端末設定の復元。"""
        if self.backend == "kitty":
            self._write_kb("\x1b[<u")  # keyboard protocol の設定を元に戻す
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None
        if self._win_console is not None:
            try:
                kernel32, handle, mode = self._win_console
                kernel32.SetConsoleMode(handle, mode)
            except Exception:
                pass
            self._win_console = None
        if self._saved is not None and self._fd is not None:
            try:
                self._termios.tcsetattr(self._fd, self._termios.TCSADRAIN, self._saved)
            except Exception:
                pass
            self._saved = None


def key_to_input(keys, state):
    """押下キー集合 -> update() 用の入力 dict。"""
    move_x = 0
    move_y = 0
    if "a" in keys or "left" in keys:
        move_x -= 1
    if "d" in keys or "right" in keys:
        move_x += 1
    if "w" in keys or "up" in keys:
        move_y -= 1
    if "s" in keys or "down" in keys:
        move_y += 1
    shoot = bool(keys & {"z", " ", "\r"})
    bomb = bool(keys & {"x"})
    quit_ = bool(keys & {"q", "\x1b"})
    state["player"]["focus"] = ("shift" in keys) or bool(keys & {"lshift", "rshift"})
    return {"move_x": move_x, "move_y": move_y, "shoot": shoot, "bomb": bomb, "quit": quit_}


def _flush_stdout(text):
    sys.stdout.write(text)
    sys.stdout.flush()


def wait_next_frame(last, tick=TICK):
    """フレーム間隔を調整し、**実際に経過した** dt を返す。

    time.sleep() は環境によって要求より早く返ることがある。
    その場合に固定値 tick を代入すると、実時間より速く時間が進んでしまうため、
    待機後に必ず時刻を測り直して実経過時間を返す。
    """
    now = time.perf_counter()
    dt = now - last
    if dt < tick:
        time.sleep(tick - dt)
        now = time.perf_counter()
        dt = now - last
    return now, min(dt, 0.25)


# ---------------------------------------------------------------------------
# 通常モード（ANSI 描画 + 端末入力ループ）
# ---------------------------------------------------------------------------
def play(input_backend="auto"):
    kb = Keyboard(force=input_backend)
    if kb.backend == "pynput" and os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
        print("[注意] Wayland 環境では pynput がキーを取得できない場合があります。")
        print("       キーが効かないときは  python touhou_like.py --input termios  で起動してください。")
        time.sleep(1.2)
    if not kb.available:
        print("[注意] この環境では端末のキー入力を取得できないため、自動デモで代わりに動作確認します。")
        print("       通常プレイは、キー入力できるターミナル（Windows: PowerShell / Linux: bash 等）で実行してください。")
        return run_main_demo(120)

    state = new_state()
    state["hi_score"] = load_highscore()
    last = time.perf_counter()
    try:
        _flush_stdout("\x1b[?25l")  # カーソル非表示
        while True:
            last, dt = wait_next_frame(last)

            active, new_keys = kb.poll()
            inp = key_to_input(active, state)
            inp["bomb"] = bool(new_keys & {"x"})  # ボムは押した瞬間だけ
            if new_keys & {"q", "\x1b"}:
                break
            if inp.get("quit"):
                break
            update(state, dt, inp)

            _flush_stdout(render_screen(state, ansi=True))
            if state["game_over"]:
                _flush_stdout(render_screen(state, ansi=True))
                _flush_stdout("\nGAME OVER  -  ENTER で終了\n")
                break
    except KeyboardInterrupt:
        pass
    finally:
        kb.close()  # POSIX では端末設定を戻す（Windows では何もしない）
        _flush_stdout("\x1b[?25h\x1b[0m\n")
        if state["score"] > load_highscore():
            save_highscore(state["score"])
        print(render_screen(state, ansi=False))
        print("SCORE=%d LIVES=%d FRAMES=%d" % (state["score"], state["player"]["lives"], state["tick"]))
    return 0


def run_main_demo(frames):
    state = run_demo(frames)
    print(render_final_frame(state))
    print("SCORE=%d" % state["score"])
    print("LIVES=%d" % max(0, state["player"]["lives"]))
    print("FRAMES=%d" % state["tick"])
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv=None):
    parser = argparse.ArgumentParser(
        description="東方Project風 ASCII 弾幕シューティング（標準ライブラリのみ）"
    )
    parser.add_argument("--demo", type=int, default=None, metavar="N",
                        help="キーボード無しで N フレーム自動プレイし、最終フレームを素のテキストで出力")
    parser.add_argument("--list", action="store_true", help="弾幕パターンとボス名を一覧表示して終了")
    parser.add_argument("--no-save", action="store_true", help="ハイスコアを保存しない")
    parser.add_argument("--input", choices=["auto", "kitty", "pynput", "termios", "msvcrt"],
                        default="auto",
                        help="入力バックエンドを強制する（kitty系端末は kitty、Waylandでpynput不可なら termios）")
    args = parser.parse_args(argv)

    if args.list:
        print("patterns: " + ", ".join(PATTERN_NAMES))
        for i in range(3):
            print("boss %d: %s" % (i, spell_card_name(i)))
        return 0

    if args.demo is not None:
        if args.demo < 0:
            print("--demo には 0 以上の整数を指定してください。", file=sys.stderr)
            return 2
        state = run_demo(args.demo)
        print(render_final_frame(state))
        print("SCORE=%d" % state["score"])
        print("LIVES=%d" % max(0, state["player"]["lives"]))
        print("FRAMES=%d" % state["tick"])
        if not args.no_save and state["score"] > 0 and state["score"] > load_highscore():
            save_highscore(state["score"])
        return 0

    return play(input_backend=args.input)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main())
