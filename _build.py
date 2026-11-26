"""段階的コミット用ビルダー。

最終版ファイルをバックアップから読み込み、機能単位に切り出した中間版を生成して
touhou_like.py / test_touhou_like.py に書き出し、git commit する。
各段階で py_compile が通ることを前提に、行範囲ではなく明確なマーカーで分割する。
"""

import io
import os
import subprocess
import sys

FINAL = io.open("_final_touhou.py", encoding="utf-8").read()
FINAL_TEST = io.open("_final_test.py", encoding="utf-8").read()


def git(*args):
    r = subprocess.run(["git"] + list(args), capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        print("GIT FAIL", args, r.stdout, r.stderr)
        sys.exit(1)
    return r.stdout


def write_main(text):
    io.open("touhou_like.py", "w", encoding="utf-8", newline="\n").write(text)


def write_test(text):
    io.open("test_touhou_like.py", "w", encoding="utf-8", newline="\n").write(text)


def check():
    r = subprocess.run([sys.executable, "-m", "py_compile", "touhou_like.py"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print("COMPILE FAIL\n", r.stdout, r.stderr)
        sys.exit(1)
    if os.path.exists("test_touhou_like.py"):
        r = subprocess.run([sys.executable, "-m", "py_compile", "test_touhou_like.py"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            print("TEST COMPILE FAIL\n", r.stdout, r.stderr)
            sys.exit(1)


def commit(msg):
    check()
    git("add", "-A")
    git("commit", "-q", "-m", msg)
    print("committed:", msg)


def between(text, start, end):
    """start マーカー行から end マーカー行の手前までを返す。"""
    i = text.index(start)
    j = text.index(end, i)
    return text[i:j]


if __name__ == "__main__":
    print("builder ready")
