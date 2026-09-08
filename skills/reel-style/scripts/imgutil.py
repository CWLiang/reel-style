#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
imgutil.py — 只用標準庫的影像取樣工具。

刻意不用 numpy：這條管線要能在「只裝了 Claude Code、沒有 pip 環境」的電腦上跑。
渲染器（render.py）本來就只用標準庫，這支讓分析與驗收也一樣，整條線就零第三方相依。

效能靠兩件事撐住，不是靠向量化：
  1. 抽影格時就用 ffmpeg 縮到很小（分析用 240-760px 就夠，不需要原尺寸）
  2. 大區域取樣用 step 跳著取，統計量不需要每個像素都算
"""
import subprocess
from collections import Counter


def _run(cmd):
    return subprocess.run(cmd, capture_output=True).stdout


def probe_size(path):
    """解碼後的尺寸（已套用旋轉）。

    用 JSON 而非 csv：ffmpeg 9.0 的 csv 輸出會多一個結尾分隔符（`1920x1080x`）。
    iPhone 直式是「1920x1080 + rotation=90」，解碼時會自動轉正，所以要換過來。
    """
    import json as _json
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height",
         "-show_entries", "stream_side_data=rotation",
         "-of", "json", path], capture_output=True, text=True).stdout
    st = _json.loads(out)["streams"][0]
    w, h = int(st["width"]), int(st["height"])
    rot = 0
    for sd in st.get("side_data_list") or []:
        if "rotation" in sd:
            rot = abs(int(sd["rotation"]))
    return (h, w) if rot in (90, 270) else (w, h)


def probe_duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", path], capture_output=True, text=True).stdout.strip()
    return float(out)


class Img:
    """一張 RGB 影格。data 是 w*h*3 的 bytes。"""

    __slots__ = ("w", "h", "d")

    def __init__(self, w, h, data):
        self.w, self.h, self.d = w, h, data

    def px(self, x, y):
        i = (y * self.w + x) * 3
        return self.d[i], self.d[i + 1], self.d[i + 2]

    def mean(self, x0, y0, x1, y1, step=1):
        """區域內所有通道的平均值（當亮度用）。"""
        d, w = self.d, self.w
        tot = n = 0
        for y in range(y0, y1, step):
            base = y * w * 3
            for x in range(x0, x1, step):
                i = base + x * 3
                tot += d[i] + d[i + 1] + d[i + 2]
                n += 3
        return tot / n if n else 0.0

    def median_rgb(self, x0, y0, x1, y1, step=2):
        """區域主色。用中位數而非平均，才不會被少數雜點拉走。"""
        d, w = self.d, self.w
        rs, gs, bs = [], [], []
        for y in range(y0, y1, step):
            base = y * w * 3
            for x in range(x0, x1, step):
                i = base + x * 3
                rs.append(d[i]); gs.append(d[i + 1]); bs.append(d[i + 2])
        if not rs:
            return (0, 0, 0)
        for a in (rs, gs, bs):
            a.sort()
        m = len(rs) // 2
        return (rs[m], gs[m], bs[m])

    def row_unlike(self, y, x0, x1, ref, thr=25, step=1):
        """該列有多少比例的像素「不像 ref 這個顏色」。用來找卡片邊界。"""
        d, w = self.d, self.w
        base = y * w * 3
        hit = n = 0
        r0, g0, b0 = ref
        for x in range(x0, x1, step):
            i = base + x * 3
            if (abs(d[i] - r0) + abs(d[i + 1] - g0) + abs(d[i + 2] - b0)) / 3 > thr:
                hit += 1
            n += 1
        return hit / n if n else 0.0

    def row_edges(self, y, x0, x1, ref, thr=25):
        """該列第一個與最後一個「不像 ref」的 x。"""
        d, w = self.d, self.w
        base = y * w * 3
        r0, g0, b0 = ref
        first = last = None
        for x in range(x0, x1):
            i = base + x * 3
            if (abs(d[i] - r0) + abs(d[i + 1] - g0) + abs(d[i + 2] - b0)) / 3 > thr:
                if first is None:
                    first = x
                last = x
        return first, last


def grab(path, t, width=None):
    """從影片抓一張 RGB 影格。width 給了就先縮小（分析不需要原尺寸）。"""
    sw, sh = probe_size(path)
    vf = "format=rgb24"
    if width:
        h = round(sh * width / sw)
        h -= h % 2
        vf += f",scale={width}:{h}"
        sw, sh = width, h
    raw = _run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", path,
                "-frames:v", "1", "-vf", vf, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"])
    need = sw * sh * 3
    if len(raw) < need:
        return None
    return Img(sw, sh, raw[:need])


def grab_gray_stream(path, vf, w, h, fps):
    """整支影片抽成一串小灰階影格，回傳 list[bytes]（每張 w*h）。"""
    raw = _run(["ffmpeg", "-v", "error", "-i", path, "-vf", vf,
                "-r", str(fps), "-f", "rawvideo", "-pix_fmt", "gray", "-"])
    n = len(raw) // (w * h)
    return [raw[i * w * h:(i + 1) * w * h] for i in range(n)]


def hexs(rgb):
    return "#%02x%02x%02x" % tuple(int(v) for v in rgb)


def dominant(img, x0, y0, x1, y1, n=4, step=2, bucket=8):
    """區域內最常出現的幾個顏色（量化到 bucket 以合併相近色）。"""
    d, w = img.d, img.w
    cnt = Counter()
    tot = 0
    for y in range(y0, y1, step):
        base = y * w * 3
        for x in range(x0, x1, step):
            i = base + x * 3
            cnt[(d[i] // bucket * bucket,
                 d[i + 1] // bucket * bucket,
                 d[i + 2] // bucket * bucket)] += 1
            tot += 1
    return [(hexs(c), round(100 * k / tot, 1)) for c, k in cnt.most_common(n)] if tot else []


def percentile(values, q):
    """q 為 0-100。"""
    if not values:
        return 0
    s = sorted(values)
    i = min(len(s) - 1, max(0, int(round((q / 100) * (len(s) - 1)))))
    return s[i]
