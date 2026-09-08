# 風格庫

風格是一份 JSON，記錄「一支影片的版面長什麼樣」——比例、色票、字級、字卡型別、動態時長。
它跟內容（`plan.json`）是分開的，所以**同一份內容可以直接換風格重出**。

---

## 現成風格

這兩份不是手工設計的，是用這個工具自己去量一支參考影片得到的。

### `split-canvas-cards`

影片縮成下方圓角卡，上方留白畫布放字卡；底色隨段落切換。

| 量到的 | 值 |
|---|---|
| 影片卡上緣 | 畫面 57% |
| 左右內縮 | 各 2.2% |
| 畫布色 | 米 `#E0D8C8` / 淺灰 `#EBE9E5` / 深 `#0A0A12` |
| 強調色 | `#D85830` |

**適合**：主體在畫面上三分之一（字卡不會壓到臉）、原片有燒錄字幕需要裁掉、資訊量大的內容。
**不適合**：主體很小、畫面細節就是重點的素材——影片被縮到 43% 會看不清楚。

### `fullscreen-overlay`

影片滿版，字卡直接疊在畫面上。上方有漸層壓暗層（scrim），字幕自動加描邊。

| 量到的 | 值 |
|---|---|
| 版面 | 滿版，無畫布 |
| scrim | 上方 62%，14 段漸層 |
| 強調色 | `#FFC24D` |
| 字幕描邊 | 7px |

**適合**：主體在下半部、背景乾淨、沒有燒錄字幕。
**不適合**：主體在上三分之一——字卡會直接壓在臉上。實測過，很醜。

---

## 你自己量出來的風格

跑過 `analyze_ref.py` 之後，草稿會自動存進：

```
~/.reel-style/styles/<參考影片檔名>.json
```

（可以用 `REEL_STYLES` 環境變數改位置。**刻意不存在 plugin 目錄裡**，那是 git clone，更新會被蓋掉。）

草稿裡分成兩種欄位：

| 狀態 | 欄位 |
|---|---|
| **已經量好** | `frame`、`video_card`、`layout_mode`、`palettes` 的 `bg` |
| **還是預設值，要改** | `needs_review` 列出來的那些：`accent`、`sizes`、`motion`、`palettes` 的 `ink`/`card`/`row` |

腳本量得出數字，量不出設計意圖。所以草稿產生後，Claude 會看 contact sheet 把 `needs_review` 那幾項補完。

用久了這個資料夾就是你自己的風格庫，之後直接指名：

```
用 <風格名> 剪這支
```

---

## 風格檔怎麼被找到

依序找四個地方，先找到先用：

1. 絕對路徑
2. `plan.json` 旁邊
3. `~/.reel-style/styles/`（你自己量的）
4. plugin 內建的 `styles/`

所以你可以用同名檔案蓋掉內建風格，不用改到 plugin。

---

## 欄位速查

```jsonc
{
  "layout_mode": "split",              // split | full，analyze_ref 會自動判定
  "frame": [1080, 1920],
  "video_card": {                      // full 模式不需要這一段
    "top_pct": 0.57,                   // 影片卡上緣佔畫面高度
    "inset_pct": 0.022,                // 左右各內縮
    "radius": 34
  },
  "accent": "#D85830",                 // 整支通常只有一個強調色
  "palettes": {                        // 每個段落挑一組用
    "beige": { "bg": "…", "ink": "…", "muted": "…", "card": "…", "row": "…", "shadow": true }
  },
  "fonts": ["PingFang TC", "Noto Sans TC", "Microsoft JhengHei"],
  "sizes": { "h1": 86, "h2": 58, "subtitle": 60 },
  "motion": {
    "card_in": 220,
    "row_in": 200,
    "section_crossfade": 260,
    "bg_hold_after": 320               // 必須 >= section_crossfade，否則換段落會暗閃
  },
  "scrim": { "color": "…", "alpha": "6E", "height_pct": 0.62, "bands": 14 }  // 只有 full 模式用
}
```

`bg_hold_after` 那條註解是修 bug 留下來的：舊底色如果比新底色的淡入早結束，
交界處會露出底層，亮度實測從 230 掉到 67。**每個踩過的坑都應該變成一個欄位，不要寫死在程式裡。**
