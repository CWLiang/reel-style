#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check.py — 成品驗收。不要跳過，也不要只憑肉眼。

    check.py <成品.mp4> [plan.json]

檢查解析度、色彩標籤、響度，以及換段落交界處的亮度是否出現暗閃
（舊底色比新底色的淡入早結束時會露出底層，實測會掉到 67）。
"""
import json, os, re, subprocess, sys

OK, BAD = "\033[32m✔\033[0m", "\033[31m�’\033[0m"


def probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height,color_transfer", "-show_entries", "format=duration",
         "-of", "json", path], capture_output=True, text=True).stdout
    d = json.loads(out)
    return d["streams"][0], float(d["format"]["duration"])


def loudness(path):
    out = subprocess.run(["ffmpeg", "-nostats", "-hide_banner", "-i", path,
                          "-af", "ebur128", "-f", "null", os.devnull],
                         capture_output=True, text=True).stderr
    m = re.search(r"Integrated loudness:\s*\n\s*I:\s*(-?[\d.]+)\s*LUFS", out)
    return float(m.group(1)) if m else None


def brightness(path, t, W):
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", path, "-frames:v", "1",
         "-vf", f"crop=w={W}:h=700:x=0:y=180,scale=200:-1,format=gray",
         "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True).stdout
    return sum(raw) / len(raw) if raw else None


def main():
    path = os.path.expanduser(sys.argv[1])
    st, dur = probe(path)
    W, H = int(st["width"]), int(st["height"])

    print(f"{OK if (W, H) == (1080, 1920) else BAD} 解析度 {W}x{H}（目標 1080x1920）")
    ct = st.get("color_transfer")
    print(f"{OK if ct == 'bt709' else BAD} 色彩 color_transfer={ct}（不能是 unknown）")
    print(f"{'  ' if 15 <= dur <= 40 else '! '} 長度 {dur:.1f} 秒"
          f"（15-30 秒為主力；30-60 秒的完播與互動實測同時墊底）")
    lu = loudness(path)
    if lu is not None:
        print(f"{OK if -17 <= lu <= -15 else BAD} 響度 {lu} LUFS（目標 -16±1）")

    if len(sys.argv) > 2:
        plan = json.load(open(os.path.expanduser(sys.argv[2]), encoding="utf-8"))
        print("\n段落交界亮度（暗閃檢查）：")
        bad = 0
        for sec in plan["sections"][1:]:
            t0 = sec["at"][0]
            vals = [brightness(path, t0 + d, W) for d in (-0.10, 0.05, 0.15, 0.30)]
            vals = [v for v in vals if v is not None]
            if not vals:
                continue
            dip = min(vals) < min(vals[0], vals[-1]) - 45
            bad += dip
            mark = BAD if dip else OK
            print(f"  {mark} t={t0:6.2f}  " + " → ".join(f"{v:5.1f}" for v in vals))
        if bad:
            print(f"\n{BAD} 有 {bad} 處暗閃：把 style.json 的 motion.bg_hold_after "
                  f"調到 >= section_crossfade（毫秒）")


if __name__ == "__main__":
    main()
