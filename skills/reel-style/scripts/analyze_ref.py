#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analyze_ref.py — 量測參考影片的版面，並產出給 Claude 看的 contact sheet。

    analyze_ref.py <參考影片> <輸出資料夾> [--reel-top N] [--reel-h N]

做兩件事：
  1. 能量測的部分自動量：畫布主色、影片卡上緣與左右內縮、內容區位置
  2. 不能量測的部分交給 Claude：抽 contact sheet，讓它看出設計語彙

⚠ 參考影片如果是手機螢幕錄影，畫面上會有 IG 自己的 UI（頂部漸層、底部按鈕列），
   那不是設計的一部分。腳本會自動偵測 9:16 內容區，偵測不到時用 --reel-top 手動指定。

只用標準庫（見 imgutil.py）。量測一律在縮到 ANALYSIS_W 的影格上做、結果以比例輸出，
所以純 Python 的逐像素迴圈也夠快。
"""
import json, os, subprocess, sys

from imgutil import grab, probe_size, probe_duration, dominant, hexs

ANALYSIS_W = 480          # 量比例不需要原解析度

# 選配：若你另外有一支「用場景偵測抽關鍵影格」的工具，設 REEL_WATCH 指向它，
# 抽出來的影格會比固定間隔更貼近版面變化的時間點。沒有就自動退回固定間隔，不影響結果。
WATCH = os.environ.get("REEL_WATCH", "")
WATCH_PY = os.environ.get("REEL_WATCH_PYTHON", "python3")


def find_reel_band(img):
    """在螢幕錄影裡找出 9:16 的內容區（回傳 top, height，單位是分析影格的像素）。"""
    H, W = img.h, img.w
    ideal = round(W * 16 / 9)
    if abs(H - ideal) < H * 0.04:
        return 0, H
    if ideal >= H:
        return 0, H
    # 內容區上緣通常在畫面上半部：找出上下反差最大的位置
    best, best_score = 0, -1.0
    for top in range(0, H - ideal + 1, 2):
        if top < 6:
            continue
        above = img.mean(0, max(0, top - 8), W, top, step=4)
        inside = img.mean(0, top, W, min(H, top + 16), step=4)
        score = abs(inside - above)
        if score > best_score:
            best, best_score = top, score
    return best, ideal


def wat_frames(path, out_dir, reel_top, reel_h, scale, limit=12):
    """用 watch skill 的場景偵測挑影格；沒裝就回空 list 讓呼叫端退回固定間隔。

    reel_top / reel_h 是分析影格的座標，scale 是「原尺寸 ÷ 分析尺寸」。
    """
    if not (WATCH and os.path.exists(WATCH)):
        return []
    wdir = os.path.join(out_dir, "_watch")
    r = subprocess.run([WATCH_PY, WATCH, path, "-o", wdir, "--no-transcript",
                        "--scene", "0.15", "--max-frames", str(limit),
                        "--resolution", "760"], capture_output=True, text=True)
    fj = os.path.join(wdir, "frames.json")
    if r.returncode != 0 or not os.path.exists(fj):
        return []
    frames = json.load(open(fj, encoding="utf-8"))
    items = frames if isinstance(frames, list) else frames.get("frames", [])
    out = []
    for i, fr in enumerate(items[:limit]):
        src = fr.get("file")
        if src and not os.path.isabs(src):
            src = os.path.join(wdir, src)
        if not (src and os.path.exists(src)):
            continue
        dst = os.path.join(out_dir, f"_ref{i:02d}.jpg")
        fw, fh = probe_size(src)
        # watch 的影格寬度未必等於原片，換算成它自己的座標再裁掉 IG 的 UI
        k = fw / (ANALYSIS_W * scale)
        top = round(reel_top * scale * k)
        hgt = round(reel_h * scale * k)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src, "-vf",
                        f"crop=w={fw}:h={min(hgt, fh - top)}:x=0:y={top},scale=380:-1",
                        "-q:v", "3", dst], check=True)
        out.append(dst)
    return out


USER_STYLES = os.path.expanduser(os.environ.get("REEL_STYLES", "~/.reel-style/styles"))
BUILTIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "styles")


def _lum(hexs):
    h = hexs.lstrip("#")
    return (int(h[0:2], 16) * 299 + int(h[2:4], 16) * 587 + int(h[4:6], 16) * 114) / 1000


def _palette_from_bg(bg):
    """由量到的畫布底色推出整組色。深底配淺字，淺底配深字。"""
    if _lum(bg) < 110:
        return {"bg": bg, "ink": "#F4F3F0", "muted": "#8C97A3",
                "card": "#151E28", "row": "#1E2A36", "shadow": False}
    return {"bg": bg, "ink": "#2B2F35", "muted": "#8A8378",
            "card": "#FBFAF7", "row": "#F0E8D8", "shadow": True}


def write_style_draft(res, name):
    """把量到的數字寫成 style.json 草稿，存進使用者自己的風格庫。

    量得到的（尺寸、影片卡比例、畫布色）直接填；量不到的（強調色、字級、
    動態時長）沿用內建風格當預設，等 Claude 看完 contact sheet 再改。
    """
    seed = "fullscreen-overlay" if res.get("layout_mode") == "full" else "split-canvas-cards"
    st = json.load(open(os.path.join(BUILTIN, seed + ".json"), encoding="utf-8"))
    st["name"] = name
    st["note"] = (f"從 {res['source']} 量出來的草稿。"
                  "尺寸／影片卡比例／畫布色是量到的；強調色、字級、動態時長還是預設值，"
                  "看過 contact sheet 之後要改。")
    st["measured_from"] = res["source"]
    st["layout_mode"] = res.get("layout_mode", "split")
    if res.get("video_card"):
        st["video_card"] = res["video_card"]
    elif "video_card" in st:
        del st["video_card"]

    # 量到的畫布色去重後排序：淺的當主色、深的當對比段落
    cands = sorted(set(res.get("palette_candidates") or []), key=_lum, reverse=True)
    if cands:
        pal, used = {}, []
        for c in cands[:3]:
            key = "dark" if _lum(c) < 110 else ("beige" if len(used) == 0 else f"light{len(used)+1}")
            if key in pal:
                continue
            pal[key] = _palette_from_bg(c)
            used.append(key)
        st["palettes"] = pal
    st["needs_review"] = ["accent", "sizes", "motion", "palettes 的 ink/card/row"]

    os.makedirs(USER_STYLES, exist_ok=True)
    out = os.path.join(USER_STYLES, name + ".json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(st, fh, ensure_ascii=False, indent=2)
    return out


def measure(path, out_dir, reel_top=None, reel_h=None):
    ow, oh = probe_size(path)
    dur = probe_duration(path)
    scale = ow / ANALYSIS_W
    os.makedirs(out_dir, exist_ok=True)

    mid = grab(path, dur * 0.55, ANALYSIS_W)
    if mid is None:
        raise SystemExit("無法讀取影片影格")
    W, H = mid.w, mid.h

    if reel_top is None or reel_h is None:
        reel_top, reel_h = find_reel_band(mid)
    else:                                        # 使用者給的是原尺寸座標
        reel_top = round(reel_top / scale)
        reel_h = round(reel_h / scale)

    res = {"source": os.path.basename(path), "size": [ow, oh], "duration": round(dur, 2),
           "reel_band_px": [round(reel_top * scale), round(reel_h * scale)]}

    samples, tops, insets = [], [], []
    for f in (0.12, 0.25, 0.40, 0.55, 0.70, 0.85):
        img = grab(path, dur * f, ANALYSIS_W)
        if img is None:
            continue
        # 畫布色：取內容區 40%~52% 高度的左右邊條（那裡通常是純底色，不會有卡片）
        y0 = reel_top + int(0.40 * reel_h)
        y1 = reel_top + int(0.52 * reel_h)
        lm = img.median_rgb(4, y0, 30, y1)
        rm = img.median_rgb(W - 30, y0, W - 4, y1)
        canvas = tuple((a + b) // 2 for a, b in zip(lm, rm))
        samples.append(hexs(canvas))

        # 影片卡上緣：從 50% 往下找第一條「幾乎整列都不是畫布色」的線
        for y in range(reel_top + int(0.50 * reel_h), reel_top + int(0.92 * reel_h)):
            if img.row_unlike(y, int(W * 0.09), int(W * 0.91), canvas, 25, step=3) > 0.85:
                tops.append((y - reel_top) / reel_h)
                yy = min(H - 1, y + round(80 / scale))
                a, b = img.row_edges(yy, 0, W, canvas, 25)
                if a is not None:
                    insets.append(min(a, W - 1 - b) / W)
                break

    def med(xs):
        return sorted(xs)[len(xs) // 2] if xs else None

    res["canvas_colors"] = samples
    # 只出現一次的顏色多半是滿版段落的影片內容滲進取樣邊條，不是真的畫布色
    from collections import Counter as _C
    freq = _C(samples)
    res["palette_candidates"] = [c for c, n in freq.most_common() if n >= 2] or sorted(set(samples))
    res["palette_rejected"] = [c for c, n in freq.items() if n < 2]
    if tops:
        res["video_card"] = {
            "top_pct": round(med(tops), 3),
            "inset_pct": round(med(insets), 3) if insets else 0.02,
            "radius": 34,
        }
        res["layout_mode"] = "split"
    else:
        res["layout_mode"] = "full"          # 找不到影片卡邊界 → 多半是滿版版面

    # --- contact sheet ---
    # 優先用 watch skill 的場景偵測挑影格：參考影片是螢幕錄影，字卡切換就是場景切換，
    # 場景偵測會剛好落在每一次版面變化上；固定間隔會漏掉短暫出現的卡片。
    jpgs = wat_frames(path, out_dir, reel_top, reel_h, scale)
    if not jpgs:
        ct, ch = round(reel_top * scale), round(reel_h * scale)
        for i in range(12):
            t = dur * (i + 0.5) / 12
            p = os.path.join(out_dir, f"_ref{i:02d}.jpg")
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", path,
                            "-frames:v", "1", "-vf",
                            f"crop=w={ow}:h={min(ch, oh - ct)}:x=0:y={ct},scale=380:-1",
                            "-q:v", "3", p], check=True)
            jpgs.append(p)

    sheets = []
    per = max(1, (len(jpgs) + 1) // 2)
    for g in range(2):
        chunk = jpgs[g * per:(g + 1) * per]
        if not chunk:
            continue
        args = []
        for f in chunk:
            args += ["-i", f]
        out = os.path.join(out_dir, f"ref_sheet{g + 1}.jpg")
        if len(chunk) == 1:
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", chunk[0], "-q:v", "3", out],
                           check=True)
        else:
            subprocess.run(["ffmpeg", "-v", "error", "-y", *args, "-filter_complex",
                            f"hstack=inputs={len(chunk)}", "-q:v", "3", out], check=True)
        sheets.append(out)
    for f in jpgs:
        if os.path.exists(f):
            os.remove(f)
    res["contact_sheets"] = sheets

    # 風格草稿存進使用者自己的風格庫，用久了就會累積成一整櫃
    slug = os.path.splitext(os.path.basename(path))[0]
    slug = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in slug).strip("-").lower()
    draft = write_style_draft(res, slug or "untitled")
    res["style_draft"] = draft

    with open(os.path.join(out_dir, "ref_measure.json"), "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=2)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    print(f"\n風格草稿已存：{draft}")
    print("   量到的數字已經填好了；強調色、字級、動態時長還是預設值。")
    print("\n接著看這兩張 contact sheet，把看到的設計語彙補進去：\n  " + "\n  ".join(sheets))
    return res


if __name__ == "__main__":
    a = sys.argv[1:]
    rt = rh = None
    if "--reel-top" in a:
        rt = int(a[a.index("--reel-top") + 1])
    if "--reel-h" in a:
        rh = int(a[a.index("--reel-h") + 1])
    pos = [x for x in a if not x.startswith("--") and not x.isdigit()]
    measure(os.path.expanduser(pos[0]), os.path.expanduser(pos[1]), rt, rh)
