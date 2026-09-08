#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analyze_raw.py — 分析要剪的原始影片，產出寫 plan.json 需要的所有素材。

    analyze_raw.py <原始影片> <輸出資料夾> [--no-asr]

輸出：
  raw_probe.json     尺寸/長度/色彩，以及偵測到的燒錄字幕帶（要裁掉的位置）
  sub_sheet*.jpg     若原片有燒錄字幕：每段字幕各抽一格拼成長圖，讓 Claude 直接讀
                     （比 ASR 準——實測 ASR 會把「鏈上金融」聽成「戀上金融」）
  transcript.json    語音辨識逐字稿（有時間碼），原片沒字幕時才是主要來源
  frames_sheet.jpg   畫面 contact sheet，用來看構圖與可用的畫面證據
"""
import json, os, subprocess, sys

from imgutil import grab_gray_stream, percentile


def probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height,r_frame_rate,color_transfer",
         "-show_entries", "format=duration", "-of", "json", path],
        capture_output=True, text=True).stdout
    d = json.loads(out)
    st = d["streams"][0]
    return {"size": [int(st["width"]), int(st["height"])],
            "duration": round(float(d["format"]["duration"]), 2),
            "fps": st.get("r_frame_rate"),
            "color_transfer": st.get("color_transfer")}


def detect_sub_band(path, W, H, dur):
    """找出燒錄字幕的黑底色塊（上/下皆可能）。回傳 (y0, y1) 或 None。"""
    hits = []
    # 縮到 240 寬再掃：字幕黑框是大色塊，不需要原解析度就找得到
    sw = 240
    sh = round(H * sw / W); sh -= sh % 2
    for f in (0.15, 0.3, 0.45, 0.6, 0.75):
        raw = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", f"{dur*f:.2f}", "-i", path, "-frames:v", "1",
             "-vf", f"format=gray,scale={sw}:{sh}", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
            capture_output=True).stdout
        if len(raw) < sw * sh:
            continue
        rows = []
        for y in range(sh):
            row = raw[y * sw:(y + 1) * sw]
            if sum(1 for v in row if v < 70) / sw > 0.55:
                rows.append(y)
        if rows:
            k = H / sh
            hits.append((round(rows[0] * k), round(rows[-1] * k)))
    if len(hits) < 3:
        return None
    y0 = sorted(h[0] for h in hits)[len(hits) // 2]
    y1 = sorted(h[1] for h in hits)[len(hits) // 2]
    # 只有落在畫面上下 45% 帶狀、且高度合理的才算字幕
    if y1 - y0 < H * 0.03 or y1 - y0 > H * 0.30:
        return None
    if H * 0.10 < y0 < H * 0.45 or y0 > H * 0.55:
        return y0, y1
    return None


def sub_segments(path, band, dur, fps=10):
    """偵測字幕換頁的時間點。"""
    y0, y1 = band
    W, H = probe(path)["size"]
    h = y1 - y0 + 10
    w, hh = 180, 36
    frames = grab_gray_stream(
        path, f"crop=w={W}:h={h}:x=0:y={max(0,y0-5)},scale={w}:{hh},format=gray",
        w, hh, fps)
    n = len(frames)
    # 二值化成「有沒有字」的位圖，再算相鄰兩幀翻轉了幾個像素
    bits = [bytes(1 if v > 150 else 0 for v in f) for f in frames]
    d = [sum(p ^ q for p, q in zip(bits[i], bits[i + 1])) for i in range(n - 1)]
    thr = max(60, percentile(d, 90) * 0.45)
    cuts = [0] + [i + 1 for i in range(len(d)) if d[i] > thr]
    merged = [cuts[0]]
    for x in cuts[1:]:
        if x - merged[-1] >= 4:
            merged.append(x)
    segs = []
    for i, cc in enumerate(merged):
        ee = merged[i + 1] if i + 1 < len(merged) else n
        if (ee - cc) / fps >= 0.65:
            segs.append([round(cc / fps, 2), round(ee / fps, 2)])
    return segs


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    src, out_dir = os.path.expanduser(args[0]), os.path.expanduser(args[1])
    os.makedirs(out_dir, exist_ok=True)
    info = probe(src)
    W, H = info["size"]
    dur = info["duration"]

    band = detect_sub_band(src, W, H, dur)
    info["burned_sub_band"] = band
    if band:
        # split layout 要把字幕帶裁掉；留 20px 餘裕
        info["suggested_crop_top"] = band[1] + 20 if band[0] < H * 0.5 else 0
        segs = sub_segments(src, band, dur)
        info["sub_segments"] = segs
        # 每段抽一格，8 段一張長圖，讓 Claude 直接讀字
        paths = []
        for i, (s, e) in enumerate(segs):
            p = os.path.join(out_dir, f"_s{i:02d}.jpg")
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{(s+e)/2:.2f}", "-i", src,
                            "-frames:v", "1", "-vf",
                            f"crop=w={W}:h={band[1]-band[0]+16}:x=0:y={max(0,band[0]-8)},"
                            f"scale=520:-1", "-q:v", "2", p], check=True)
            paths.append(p)
        sheets = []
        for g in range((len(paths) + 7) // 8):
            chunk = paths[g * 8:(g + 1) * 8]
            a = []
            for f in chunk:
                a += ["-i", f]
            o = os.path.join(out_dir, f"sub_sheet{g}.jpg")
            if len(chunk) == 1:
                os.replace(chunk[0], o)
            else:
                subprocess.run(["ffmpeg", "-v", "error", "-y", *a, "-filter_complex",
                                f"vstack=inputs={len(chunk)}", "-q:v", "3", o], check=True)
            sheets.append(o)
        for f in paths:
            if os.path.exists(f):
                os.remove(f)
        info["sub_sheets"] = sheets
    else:
        info["suggested_crop_top"] = 0

    # 畫面 contact sheet
    jp = []
    for i in range(8):
        p = os.path.join(out_dir, f"_f{i}.jpg")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{dur*(i+0.5)/8:.2f}",
                        "-i", src, "-frames:v", "1", "-vf", "scale=270:-1", "-q:v", "3", p],
                       check=True)
        jp.append(p)
    a = []
    for f in jp:
        a += ["-i", f]
    fs = os.path.join(out_dir, "frames_sheet.jpg")
    subprocess.run(["ffmpeg", "-v", "error", "-y", *a, "-filter_complex",
                    "hstack=inputs=8", "-q:v", "3", fs], check=True)
    for f in jp:
        os.remove(f)
    info["frames_sheet"] = fs

    # 語音辨識（原片已有燒錄字幕時只當備援）
    if "--no-asr" not in sys.argv:
        try:
            ad = os.path.join(out_dir, "_aud")
            os.makedirs(ad, exist_ok=True)
            mp3 = os.path.join(ad, "a.mp3")
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src, "-vn",
                            "-ac", "1", "-ar", "16000", "-b:a", "24k", mp3], check=True)
            here = os.path.dirname(os.path.abspath(__file__))
            tr = os.path.join(here, "transcribe.py")
            if os.path.exists(tr):
                subprocess.run([sys.executable, tr, ad, out_dir], check=True)
                info["transcript"] = os.path.join(out_dir, "a.json")
        except Exception as ex:                       # 轉錄失敗不該擋住整條流程
            info["asr_error"] = str(ex)

    with open(os.path.join(out_dir, "raw_probe.json"), "w", encoding="utf-8") as fh:
        json.dump(info, fh, ensure_ascii=False, indent=2)
    print(json.dumps(info, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
