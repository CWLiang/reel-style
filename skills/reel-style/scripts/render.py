#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
render.py — style.json + plan.json → 直式短影音成品。

    render.py plan.json [still 12.5 34.0 ...]

風格（版面比例、色票、字級、動態時長）全部來自 style.json；
內容（分段、字卡、字幕）全部來自 plan.json。這支不含任何硬編碼的版面數值，
換一個參考影片只要換 style.json。

版面模型（split layout）：
  影片縮成下方圓角卡，上方留白畫布放字卡。畫布用 ASS 的「挖洞」形狀畫出來，
  所以整支只需要一次 ffmpeg pass。
"""
import json, os, subprocess, sys

# ---------------- 色彩 ----------------

def c(hexs, alpha="00"):
    """#RRGGBB -> ASS &HAABBGGRR&（ASS 是 BGR，alpha 反向：00 不透明）"""
    h = hexs.lstrip("#")
    return f"&H{alpha}{h[4:6]}{h[2:4]}{h[0:2]}&"


def ts(sec):
    sec = max(0.0, sec)
    return f"{int(sec//3600)}:{int(sec%3600//60):02d}:{sec%60:05.2f}"


# ---------------- 繪圖 ----------------

def rrect(x, y, w, h, r):
    r = max(0, min(r, min(w, h) // 2))
    k = round(r * 0.5523)
    x2, y2 = x + w, y + h
    return (f"m {x+r} {y} l {x2-r} {y} "
            f"b {x2-r+k} {y} {x2} {y+r-k} {x2} {y+r} l {x2} {y2-r} "
            f"b {x2} {y2-r+k} {x2-r+k} {y2} {x2-r} {y2} l {x+r} {y2} "
            f"b {x+r-k} {y2} {x} {y2-r+k} {x} {y2-r} l {x} {y+r} "
            f"b {x} {y+r-k} {x+r-k} {y} {x+r} {y}")


def circle(cx, cy, r):
    k = round(r * 0.5523)
    return (f"m {cx-r} {cy} b {cx-r} {cy-k} {cx-k} {cy-r} {cx} {cy-r} "
            f"b {cx+k} {cy-r} {cx+r} {cy-k} {cx+r} {cy} "
            f"b {cx+r} {cy+k} {cx+k} {cy+r} {cx} {cy+r} "
            f"b {cx-k} {cy+r} {cx-r} {cy+k} {cx-r} {cy}")


class Reel:
    def __init__(self, style, plan):
        self.st, self.pl = style, plan
        self.W, self.H = style["frame"]
        vc = style.get("video_card") or {}
        # 版面模型是風格的一部分，不是寫死的：
        #   split —— 影片縮成下方圓角卡，上方留白畫布放字卡
        #   full  —— 影片滿版，字卡直接疊在畫面上（字幕需要描邊）
        self.mode = style.get("layout_mode", "split" if vc else "full")
        if self.mode == "split":
            self.card_x = round(self.W * vc["inset_pct"])
            self.card_w = self.W - self.card_x * 2
            self.card_y = round(self.H * vc["top_pct"])
            self.card_r = vc["radius"]
        else:
            self.card_x, self.card_w, self.card_y, self.card_r = 0, self.W, 0, 0
        self.sz = style["sizes"]
        self.mo = style["motion"]
        self.accent = style["accent"]
        self.dur = plan["duration"]

        # 版面帶（全部由 video_card 位置推導，換風格自動跟著動）
        self.credit_y = round(self.H * 0.0896)
        self.kicker_y = round(self.H * 0.130)
        self.zone_top = round(self.H * 0.156)
        if self.mode == "split":
            self.sub_bottom = self.card_y - 62
            self.zone_bottom = self.sub_bottom - 150
        else:
            # 滿版時 IG 下方約 380px 會被貼文文案與按鈕蓋住
            self.sub_bottom = self.H - 380
            self.zone_bottom = round(self.H * 0.56)
        self.EV = []

    # -------- 事件 --------
    def ev(self, layer, s, e, style, text):
        self.EV.append(f"Dialogue: {layer},{ts(s)},{ts(e)},{style},,0,0,0,,{text}")

    def shape(self, layer, s, e, path, fill, alpha="00", extra=""):
        self.ev(layer, s, e, "Fx",
                f"{{\\an7\\pos(0,0)\\1c{c(fill)}\\bord0\\shad0\\1a&H{alpha}&{extra}}}"
                f"{{\\p1}}{path}{{\\p0}}")

    def txt(self, layer, s, e, style, x, y, body, an=5, extra=""):
        self.ev(layer, s, e, style, f"{{\\an{an}\\pos({x},{y}){extra}}}{body}")

    def fade(self, i=None, o=None):
        i = self.mo["card_in"] if i is None else i
        o = 140 if o is None else o
        return f"\\fad({i},{o})"

    # -------- 卡片（含假陰影）--------
    def cardbox(self, layer, s, e, x, y, w, h, pal, extra=""):
        if y + h > self.zone_bottom:
            print(f"⚠ 卡片超出可用高度 {y+h} > {self.zone_bottom}（t={s:.1f}）"
                  f"：減少列數或縮短內容，否則會疊到字幕", file=sys.stderr)
        if pal.get("shadow", True):
            # ASS 沒有模糊，用遞減 alpha 疊三層近似柔邊，否則白卡在淺底上會消失
            for grow, off, a in ((14, 12, "F2"), (8, 8, "EA"), (3, 4, "E2")):
                self.shape(layer, s, e,
                           rrect(x - grow, y - grow + off, w + grow * 2, h + grow * 2, 44 + grow),
                           "#6E6459", alpha=a, extra=extra)
        self.shape(layer, s, e, rrect(x, y, w, h, 44), pal["card"], extra=extra)

    # -------- 關鍵詞染色 --------
    def hl(self, line, base):
        out, on = "", False
        for part in line.split("**"):
            if part:
                out += f"{{\\c{c(self.accent) if on else c(base)}}}{part}"
            on = not on
        return out or f"{{\\c{c(base)}}}"

    # ================= 主流程 =================
    def build(self):
        st, pl = self.st, self.pl
        secs = pl["sections"]

        # 1. 畫布底色（挖出影片卡的洞）。滿版模式沒有畫布，跳過。
        if self.mode == "split":
            hole = self._hole()
            for i, sec in enumerate(secs):
                s, e = sec["at"]
                pal = st["palettes"][sec["palette"]]
                fd = f"\\fad({self.mo['section_crossfade']},0)" if i else "\\fad(0,0)"
                # 舊底色要撐過新底色的淡入，否則交界處露出底層 pad 色 → 暗閃
                hold = self.mo["bg_hold_after"] / 1000 if e < self.dur else 0
                self.shape(0, s, e + hold, hole, pal["bg"], extra=fd)

        # 1b. 滿版模式的壓暗層：字卡沒有畫布可靠，需要一層 scrim 才讀得到
        scrim = st.get("scrim")
        if self.mode == "full" and scrim:
            sh = round(self.H * scrim.get("height_pct", 0.52))
            col = scrim.get("color", "#000000")
            a0 = int(scrim.get("alpha", "8C"), 16)
            # ASS 沒有漸層，切成多條各自 alpha 的橫帶近似，否則底部會出現一條硬邊
            bands = scrim.get("bands", 14)
            for i in range(bands):
                y0 = round(sh * i / bands)
                y1 = round(sh * (i + 1) / bands)
                t = i / (bands - 1)
                a = round(a0 + (255 - a0) * t * t)          # 平方讓上半段維持不透明
                self.shape(0, 0, self.dur,
                           f"m 0 {y0} l {self.W} {y0} {self.W} {y1} 0 {y1}",
                           col, alpha=f"{min(255, a):02X}")

        # 2. 頂部署名小字
        if st.get("credit") and pl.get("credit"):
            for sec in secs:
                s, e = sec["at"]
                pal = st["palettes"][sec["palette"]]
                self.txt(6, s, e, "Credit", self.W // 2, self.credit_y,
                         f"{{\\c{c(pal['muted'])}}}{pl['credit']}")

        # 3. 每段的字卡
        for sec in secs:
            self._section(sec)

        # 4. 字幕
        for item in pl["subs"]:
            s, e, l1 = item[0], item[1], item[2]
            l2 = item[3] if len(item) > 3 else ""
            pal = st["palettes"][self._palette_at(s)]
            if l2:
                body = self.hl(l1, pal["ink"]) + "\\N" + self.hl(l2, pal["ink"])
                y = self.sub_bottom - 128
            else:
                body = self.hl(l1, pal["ink"])
                y = self.sub_bottom - 62
            self.txt(5, s, e, "Sub2", self.W // 2, y, body, extra="\\fad(140,110)")

        return self._ass()

    def _palette_at(self, t):
        for sec in self.pl["sections"]:
            if sec["at"][0] <= t < sec["at"][1]:
                return sec["palette"]
        return self.pl["sections"][-1]["palette"]

    def _hole(self):
        W, H = self.W, self.H
        x0, x1, y = self.card_x, self.card_x + self.card_w, self.card_y
        r = self.card_r
        k = round(r * 0.5523)
        return " ".join([
            f"m 0 0 l {W} 0 {W} {y} 0 {y}",
            f"m 0 {y} l {x0} {y} {x0} {H} 0 {H}",
            f"m {x1} {y} l {W} {y} {W} {H} {x1} {H}",
            f"m {x0} {y+r} l {x0} {y} l {x0+r} {y} b {x0+r-k} {y} {x0} {y+r-k} {x0} {y+r}",
            f"m {x1-r} {y} l {x1} {y} l {x1} {y+r} b {x1} {y+r-k} {x1-r+k} {y} {x1-r} {y}",
        ])

    # ================= 段落 =================
    def _section(self, sec):
        s, e = sec["at"]
        e_ = e + 0.05
        pal = self.st["palettes"][sec["palette"]]
        cx = self.W // 2

        if sec.get("kicker"):
            spaced = " ".join(sec["kicker"])
            self.txt(3, s, e, "Kicker", cx, self.kicker_y,
                     f"{{\\c{c(self.accent)}\\fsp6}}{spaced}", extra="\\fad(240,120)")

        card = sec.get("card")
        bottom = self.zone_top
        if card:
            bottom = getattr(self, f"_card_{card['type']}")(s, e_, card, pal)

        if sec.get("pill"):
            p = sec["pill"]
            at = p.get("at", s)
            py = bottom + 46
            w = max(360, len(p["text"]) * 34 + 90)
            self.shape(2, at, e_, rrect(cx - w // 2, py - 37, w, 74, 37), self.accent,
                       extra=self.fade())
            self.txt(4, at, e_, "Pill", cx, py, f"{{\\c&H00FFFFFF&}}{p['text']}",
                     extra=self.fade(260))

    # -------- 卡片型別 --------
    def _card_title(self, s, e, card, pal):
        """開場標題卡：小字 + 大標 + 分隔線 + 說明"""
        cx, x, w = self.W // 2, 110, self.W - 220
        top = self.zone_top
        h = 420
        self.cardbox(2, s, e, x, top, w, h, pal, extra="\\fad(200,150)")
        y = top + 72
        if card.get("eyebrow"):
            self.txt(3, s, e, "Kicker", cx, y,
                     f"{{\\c{c(self.accent)}\\fsp10}}{' '.join(card['eyebrow'])}",
                     extra="\\fad(260,150)")
            y += 100
        self.txt(3, s + 0.15, e, "H1", cx, y, f"{{\\c{c(pal['ink'])}}}{card['title']}",
                 extra="\\fad(200,150)\\fscx86\\fscy86\\t(0,150,\\fscx103\\fscy103)"
                       "\\t(150,240,\\fscx100\\fscy100)")
        y += 80
        self.shape(2, s + 0.35, e, rrect(cx - 110, y, 220, 5, 2), self.accent,
                   extra="\\fad(240,150)")
        if card.get("caption"):
            self.txt(3, s + 0.5, e, "Body", cx, y + 78,
                     f"{{\\c{c(pal['muted'])}}}{card['caption']}", extra="\\fad(300,150)")
        return top + h

    def _card_list(self, s, e, card, pal):
        """標題 + 逐一淡入的列項（可帶副標與編號徽章）"""
        rows = card["rows"]
        has_sub = any(r.get("sub") for r in rows)
        rh, gap = (96, 10) if has_sub else (86, 12)
        return self._rows_card(s, e, card, pal, rows, rh, gap, kind="list")

    def _card_flow(self, s, e, card, pal):
        """標題 + 以 ↓ 串起的流程列，最後一列可高亮"""
        return self._rows_card(s, e, card, pal, card["rows"], 92, 30, kind="flow")

    def _rows_card(self, s, e, card, pal, rows, rh, gap, kind):
        x, w = 90, self.W - 180
        top = self.zone_top
        title_h = 124 if card.get("title") else 40
        body_h = len(rows) * rh + (len(rows) - 1) * gap
        h = title_h + body_h + 28
        self.cardbox(2, s, e, x, top, w, h, pal, extra=self.fade())
        if card.get("title"):
            self.txt(3, s, e, "H2", self.W // 2, top + 68,
                     f"{{\\c{c(pal['ink'])}}}{card['title']}", extra=self.fade(260))

        rx, rw = x + 36, w - 72
        # 深底上的 ghost 要更不透明才看得見（淺底 25%、深底 37%）
        bg = pal["bg"].lstrip("#")
        lum = (int(bg[0:2], 16) * 299 + int(bg[2:4], 16) * 587 + int(bg[4:6], 16) * 114) / 1000
        ghost_a = "A0" if lum < 110 else "C0"
        for i, r in enumerate(rows):
            at = r.get("at", s)
            ry = top + title_h + i * (rh + gap)
            if at > s + 0.25:
                self.shape(2, s + 0.25, at, rrect(rx, ry, rw, rh, 26), pal["row"],
                           alpha=ghost_a, extra="\\fad(200,0)")
            hi = r.get("hi")
            self.shape(2, at, e, rrect(rx, ry, rw, rh, 26),
                       self.accent if hi else pal["row"], extra=self.fade(200))
            ink = "&H00FFFFFF&" if hi else c(pal["ink"])

            if r.get("badge"):
                self.shape(3, at, e, circle(rx + 60, ry + rh // 2, 28), self.accent,
                           extra=self.fade(200))
                self.txt(4, at, e, "Badge", rx + 60, ry + rh // 2,
                         f"{{\\c&H00FFFFFF&}}{r['badge']}", extra=self.fade(220))
                tx, an = rx + 114, 4
            elif r.get("bullet"):
                self.txt(4, at, e, "Row", rx + 50, ry + rh // 2,
                         f"{{\\c{c(self.accent)}}}{r['bullet']}", an=4, extra=self.fade(220))
                tx, an = rx + 106, 4
            else:
                tx, an = self.W // 2, 5

            if r.get("sub"):
                self.txt(4, at, e, "Name", tx, ry + 34, f"{{\\c{ink}}}{r['text']}",
                         an=an, extra=self.fade(240))
                self.txt(4, at, e, "Sub", tx, ry + 70, f"{{\\c{c(pal['muted'])}}}{r['sub']}",
                         an=an, extra=self.fade(260))
            else:
                self.txt(4, at, e, "Row", tx, ry + rh // 2, f"{{\\c{ink}}}{r['text']}",
                         an=an, extra=self.fade(240))

            if kind == "flow" and i < len(rows) - 1:
                self.txt(3, at, e, "Arrow", self.W // 2, ry + rh + gap // 2,
                         f"{{\\c{c(pal['muted'])}}}↓", extra=self.fade(240))
        return top + h

    def _card_bars(self, s, e, card, pal):
        """標題 + 橫條圖表（\\clip 動畫填充）+ 註記"""
        x, w = 90, self.W - 180
        top = self.zone_top
        bars = card["bars"]
        title_h = 124 if card.get("title") else 40
        body_h = len(bars) * 118
        note_h = 56 if card.get("note") else 0
        h = title_h + body_h + note_h + 16
        self.cardbox(2, s, e, x, top, w, h, pal, extra=self.fade(220, 160))
        if card.get("title"):
            self.txt(3, s, e, "H2", self.W // 2, top + 68,
                     f"{{\\c{c(pal['ink'])}}}{card['title']}", extra=self.fade(260, 160))
        tx, tw = x + 62, w - 124
        for i, b in enumerate(bars):
            at = b.get("at", s)
            by = top + title_h + i * 118
            self.txt(4, at, e, "Row", tx, by, f"{{\\c{c(pal['ink'])}}}{b['label']}",
                     an=4, extra=self.fade(240, 160))
            self.txt(4, at, e, "Row", tx + tw, by, f"{{\\c{c(self.accent)}}}{b['pct']}%",
                     an=6, extra=self.fade(240, 160))
            self.shape(2, at, e, rrect(tx, by + 26, tw, 18, 9),
                       card.get("track", "#E4DFD5"), extra=self.fade(240, 160))
            fw = round(tw * b["pct"] / 100)
            self.shape(3, at, e, rrect(tx, by + 26, fw, 18, 9), self.accent,
                       extra=f"{self.fade(240,160)}\\clip({tx},{by+26},{tx},{by+44})"
                             f"\\t(180,900,\\clip({tx},{by+26},{tx+fw},{by+44}))")
        if card.get("note"):
            self.txt(4, bars[0].get("at", s), e, "Note", self.W // 2,
                     top + title_h + body_h + 22,
                     f"{{\\c{c(pal['muted'])}}}{card['note']}", extra=self.fade(300, 160))
        return top + h

    def _card_scope(self, s, e, card, pal):
        """標題 + 一排以 → 串起的方塊（範圍/流向）"""
        x, w = 90, self.W - 180
        top = self.zone_top
        items = card["items"]
        title_h = 124 if card.get("title") else 40
        h = title_h + 108 + 28
        self.cardbox(2, s, e, x, top, w, h, pal, extra=self.fade(240))
        if card.get("title"):
            self.txt(3, s, e, "H2", self.W // 2, top + 68,
                     f"{{\\c{c(pal['ink'])}}}{card['title']}", extra=self.fade(280))
        n = len(items)
        gap = 36
        bw = (w - 72 - (n - 1) * gap) // n
        for i, it in enumerate(items):
            at = it.get("at", s)
            bx = x + 36 + i * (bw + gap)
            by = top + title_h
            self.shape(2, at, e, rrect(bx, by, bw, 108, 28), pal["row"], extra=self.fade(220))
            self.txt(4, at, e, "Row", bx + bw // 2, by + 54,
                     f"{{\\c{c(pal['ink'])}}}{it['text']}", extra=self.fade(240))
            if i < n - 1:
                self.txt(3, at + 0.3, e, "Arrow", bx + bw + gap // 2, by + 54,
                         f"{{\\c{c(self.accent)}}}→", extra=self.fade(200))
        return top + h

    def _card_quote(self, s, e, card, pal):
        """引述卡：出處小字 + 陳述 + ↓ + 高亮結論"""
        x, w = 90, self.W - 180
        top = self.zone_top
        has_con = bool(card.get("conclusion"))
        h = (124 if card.get("title") else 40) + 130 + (30 + 106 if has_con else 0) + 28
        self.cardbox(2, s, e, x, top, w, h, pal, extra=self.fade(240))
        y = top
        if card.get("title"):
            self.txt(3, s, e, "H2", self.W // 2, top + 68,
                     f"{{\\c{c(pal['ink'])}}}{card['title']}", extra=self.fade(280))
            y = top + 124
        else:
            y = top + 40
        rx, rw = x + 36, w - 72
        self.shape(2, s + 0.2, e, rrect(rx, y, rw, 130, 28), pal["row"], extra=self.fade(240))
        self.txt(4, s + 0.2, e, "Quote", self.W // 2, y + 42,
                 f"{{\\c{c(pal['muted'])}}}{card['source']}", extra=self.fade(260))
        st_at = card.get("statement_at", s + 0.2)
        self.txt(4, st_at, e, "Row", self.W // 2, y + 92,
                 self.hl(card["statement"], pal["ink"]), extra=self.fade(240))
        if has_con:
            con = card["conclusion"]
            at = con.get("at", s)
            self.txt(3, at, e, "Arrow", self.W // 2, y + 168,
                     f"{{\\c{c(pal['muted'])}}}↓", extra=self.fade(240))
            self.shape(2, at, e, rrect(rx, y + 206, rw, 106, 28), self.accent,
                       extra=self.fade(240))
            self.txt(4, at, e, "Row", self.W // 2, y + 259,
                     f"{{\\c&H00FFFFFF&}}{con['text']}", extra=self.fade(260))
        return top + h

    def _card_hero(self, s, e, card, pal):
        """無卡框的大字堆疊（大數字、兩詞相乘等）"""
        cx = self.W // 2
        lines = card["lines"]
        y = self.zone_top + card.get("offset", 60)
        for ln in lines:
            at = ln.get("at", s)
            style = ln.get("style", "H1")
            col = {"accent": self.accent, "ink": pal["ink"], "muted": pal["muted"]}[
                ln.get("color", "ink")]
            pop = ("\\fad(160,140)\\fscx72\\fscy72\\t(0,160,\\fscx105\\fscy105)"
                   "\\t(160,260,\\fscx100\\fscy100)") if ln.get("pop") else self.fade(240)
            self.txt(3, at, e, style, cx, y, f"{{\\c{c(col)}}}{ln['text']}", extra=pop)
            y += ln.get("gap", 150)
        return y - 60

    # ================= 輸出 =================
    def _ass(self):
        font = self.st["fonts"][0]
        rows = [
            ("Fx", 40, 0), ("Credit", self.sz["credit"], 0), ("Kicker", self.sz["kicker"], 1),
            ("H1", self.sz["h1"], 1), ("H2", self.sz["h2"], 1), ("Huge", self.sz["huge"], 1),
            ("Hero", self.sz["hero"], 1), ("Body", self.sz["body"], 0), ("Row", self.sz["row"], 1),
            ("Name", self.sz["name"], 1), ("Sub", self.sz["sub"], 0), ("Quote", 38, 0),
            ("Badge", self.sz["badge"], 1), ("Pill", self.sz["pill"], 1),
            ("Note", self.sz["note"], 0), ("Arrow", self.sz["arrow"], 1),
            ("Sub2", self.sz["subtitle"], 1),
        ]
        # 滿版時字幕直接壓在畫面上，描邊要夠粗（手機小、背景常是亮天空或白牆）
        outline = self.st.get("subtitle_outline", 6 if self.mode == "full" else 0)
        head = (f"[Script Info]\nScriptType: v4.00+\nPlayResX: {self.W}\nPlayResY: {self.H}\n"
                "WrapStyle: 2\nScaledBorderAndShadow: yes\nYCbCr Matrix: TV.709\n\n"
                "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
                "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, "
                "Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, "
                "MarginV, Encoding\n")
        for name, size, bold in rows:
            ol = outline if name == "Sub2" else 0
            head += (f"Style: {name},{font},{size},&H00FFFFFF&,&H000000FF&,&H00000000&,"
                     f"&H00000000&,{bold},0,0,0,100,100,0,0,1,{ol},{ol//3},5,0,0,0,1\n")
        head += ("\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, "
                 "MarginV, Effect, Text\n")
        return head + "\n".join(self.EV) + "\n"


def probe_src(path):
    """回傳 (解碼後的寬, 高, 是否 HDR)。

    iPhone 直式是「1920x1080 + rotation=90」，ffmpeg 解碼時會自動轉正，
    所以濾鏡鏈要用轉正後的尺寸，不能直接拿 ffprobe 的 width/height。
    """
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height,color_transfer", "-show_entries", "stream_side_data=rotation",
         "-of", "json", path], capture_output=True, text=True).stdout
    d = json.loads(out)["streams"][0]
    w, h = int(d["width"]), int(d["height"])
    rot = 0
    for sd in d.get("side_data_list") or []:
        if "rotation" in sd:
            rot = abs(int(sd["rotation"]))
    if rot in (90, 270):
        w, h = h, w
    hdr = d.get("color_transfer") in ("arib-std-b67", "smpte2084")
    return w, h, hdr


# iPhone 預設錄 HLG HDR。不 tone map 直接解成 8-bit 會刷白（實測飽和度 30% → 18%）。
# 必須從原始 10-bit 做，所以放在濾鏡鏈最前面。
TONEMAP = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
           "tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p,")


def vfilter(style, plan, ass_path):
    W, H = style["frame"]
    vc = style.get("video_card") or {}
    mode = style.get("layout_mode", "split" if vc else "full")
    crop_top0 = plan.get("crop_top", 0)
    dw, dh, hdr = probe_src(os.path.expanduser(plan["source"]))
    src_w0, src_h0 = plan.get("source_size") or [dw, dh]
    pre = TONEMAP if plan.get("hdr", hdr) else ""
    if mode == "full":
        return (pre + f"crop=w={src_w0}:h={src_h0-crop_top0}:x=0:y={crop_top0},"
                f"scale={W}:{H}:force_original_aspect_ratio=increase,"
                f"crop=w={W}:h={H},ass={ass_path}")
    cx = round(W * vc["inset_pct"])
    cw = W - cx * 2
    cy = round(H * vc["top_pct"])
    crop_top = crop_top0
    src_w, src_h = src_w0, src_h0
    keep_h = src_h - crop_top
    scaled_h = round(keep_h * cw / src_w)
    card_h = H - cy
    off = plan.get("crop_offset", 0)
    return (pre + f"crop=w={src_w}:h={keep_h}:x=0:y={crop_top},"
            f"scale={cw}:{scaled_h},"
            f"crop=w={cw}:h={min(card_h, scaled_h)}:x=0:y={off},"
            f"pad={W}:{H}:{cx}:{cy}:color=0x111111,"
            f"ass={ass_path}")


def main():
    plan_path = sys.argv[1]
    plan = json.load(open(plan_path, encoding="utf-8"))
    base = os.path.dirname(os.path.abspath(plan_path))
    sp = plan["style"]
    if not os.path.isabs(sp):
        sp = os.path.join(base, sp)
        if not os.path.exists(sp):
            sp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", plan["style"])
    style = json.load(open(sp, encoding="utf-8"))

    out_dir = os.path.expanduser(plan.get("out_dir") or base)
    os.makedirs(out_dir, exist_ok=True)
    ass_path = os.path.join(out_dir, "overlay.ass")
    ass = Reel(style, plan).build()
    open(ass_path, "w", encoding="utf-8").write(ass)
    print(f"ASS 已產生：{ass_path}（{ass.count('Dialogue:')} 個事件）")

    src = os.path.expanduser(plan["source"])
    vf = vfilter(style, plan, ass_path)

    if len(sys.argv) > 2 and sys.argv[2] == "still":
        for at in sys.argv[3:]:
            out = os.path.join(out_dir, f"still_{at.replace('.', '_')}.jpg")
            # -ss 必須放在 -i 後面（output seeking），放前面會讓 ASS 時間軸歸零
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src, "-ss", at,
                            "-vf", vf, "-frames:v", "1", "-q:v", "2", out], check=True)
            print("->", out)
        return

    out = os.path.join(out_dir, plan.get("out_name", "output.mp4"))
    # 響度正規化：手機直錄常落在 -22 LUFS 左右，在 IG 動態裡會明顯比別人小聲。
    # 目標 -16 LUFS、真峰值 -1.5 dBTP。設 plan 的 "loudnorm": false 可關掉。
    af = []
    if plan.get("loudnorm", True):
        af = ["-af", "loudnorm=I=-16:TP=-1.5:LRA=11"]
    subprocess.run(["ffmpeg", "-v", "error", "-stats", "-y", "-i", src, "-vf", vf, *af,
                    "-c:v", "libx264", "-preset", "medium", "-crf", "19",
                    "-pix_fmt", "yuv420p", "-colorspace", "bt709",
                    "-color_primaries", "bt709", "-color_trc", "bt709",
                    "-color_range", "tv", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out], check=True)
    print("->", out)


if __name__ == "__main__":
    main()
