# 踩過的坑

按「最容易毀掉整批成品」排序。

## 1. iPhone HDR：畫面被刷白

**症狀**：成品比原始檔灰、奶白、皮膚沒血色、綠葉變灰綠。使用者的說法通常是「顏色跑掉了」「整個被刷白」。

**原因**：iPhone 預設錄 **HLG HDR**（`color_transfer=arib-std-b67`、`color_primaries=bt2020`、10-bit）。直接解碼成 8-bit SDR 而不做 tone mapping，等於拿 HLG 的亮度曲線當一般 gamma 曲線解讀 —— 實測飽和度從 30% 掉到 18%，四成的顏色在第一步就沒了。

**還有第二層**：套完濾鏡後色彩標籤常常整個掉成 `unknown`。macOS/iOS 對「有 HLG 標籤」的檔案會自動 tone map，所以**沒處理但標籤還在**的版本看起來正常，**處理過但標籤掉了**的版本看起來反而更糟。這會讓人誤判成「是特效害的」。

**解法**（`reel.py` 的 `cut` 會自動偵測並套用）：

```
zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,
tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p
```

加上每一次輸出都明確寫入標籤：

```
-colorspace bt709 -color_primaries bt709 -color_trc bt709 -color_range tv
```

**要點**：
- tone map 要從**原始 10-bit 檔**做，不要從壓過的 8-bit 中間檔（會有斷階）
- `hable` 比 `mobius` / `reinhard` 自然，`desat=0` 保留飽和度
- 需要 ffmpeg 有 `--enable-libzimg`。沒有的話 `colorspace` 濾鏡處理不了 HLG 曲線，只能請使用者換環境
- **轉換後調色要收手**：底層顏色回來了，原本疊在「刷白版」上的 +17% 飽和會變過火。改成 +9% 左右

驗證方法（別只用眼睛）：

```python
# 抽一格算平均飽和度，跟正確 tone map 的參考值比
mx, mn = rgb.max(1), rgb.min(1)
sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1), 0).mean() * 100
```

## 2. Cowork device_bash 的限制

在使用者電腦上跑指令時：

- **每次呼叫上限 45 秒**，超過就整個中斷
- **背景程序留不住**：`nohup`、`setsid`、`disown` 全部無效，呼叫結束就被殺。不要試了
- **每次都是新的 `bash -c`**：cwd 和環境變數不會延續，一律用絕對路徑
- **沒有網路**：pip / npm / curl 全部不通。要裝東西或下載模型，得在雲端容器做
- **不能刪檔**：`rm` 會回 Operation not permitted。要「刪」就 `mv` 到一個 `_to_delete/` 資料夾，再告訴使用者自己刪
- **bridge 會斷線**：使用者闔上筆電就斷。工作要切成小步，每步的結果直接寫進他的資料夾，不要累積在記憶體裡

實務上一支 40 秒的影片：`cut` 約 30 秒、`render` 約 21 秒、`mix` 約 6 秒。**一次呼叫跑一個指令**就好，貪心合併很容易超時。

## 3. 沙箱裡跑不動 Whisper

`huggingface.co`、`cdn-lfs.huggingface.co`、`openaipublic.azureedge.net` 全部被 proxy 擋（403 或連線失敗）。`faster-whisper` 和 `openai-whisper` 都會在下載權重時死掉。

**通的**：`pypi.org`、`files.pythonhosted.org`、`registry.npmjs.org`、`github.com`（含 releases 與 raw）。

所以改用 **sherpa-onnx + SenseVoice**，權重掛在 sherpa-onnx 的 GitHub Releases。中文辨識比 Whisper small/medium 更準，速度也快。`scripts/transcribe.py` 已經包好。

流程：在使用者電腦抽 mp3 → stage 進容器 → 在容器跑辨識。音檔很小（25 分鐘約 7MB），影片才大。

## 4. `-ss` 的位置決定快慢

```bash
ffmpeg -ss 300 -i big.MOV -frames:v 1 out.jpg      # 0.3 秒（input seeking）
ffmpeg -i big.MOV -ss 300 -frames:v 1 out.jpg      # 從頭解碼，可能好幾十秒
```

抽縮圖一律把 `-ss` 放 `-i` 前面。同理，用 `fps=` 濾鏡掃過整支影片來抽格會解碼全片，一批檔案就會超時 —— 改成逐點 `-ss` 抽。

## 5. ffmpeg filter graph 的兩個常見錯誤

**標籤只能被消費一次**。`[vo]` 同時餵給 `sidechaincompress` 和 `amix` 會失敗，要先 `asplit=2[vo][vosc]`。

**sidechaincompress 需要兩邊格式一致**，否則報 `No channel layout for input 1`。每條音訊支路後面都加：

```
aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo
```

## 6. 旋轉標籤

iPhone 直式影片是「1920x1080 + `rotate=270`」。ffprobe 的 `stream=width,height` 顯示 1920x1080，但 ffmpeg 解碼時會自動轉正成 1080x1920。

**不要看到 1920x1080 就以為要裁切**。先抽一格出來看實際輸出尺寸再決定。同一批素材可能混著 `rotate=90` 和 `rotate=270`（手機拿的方向不同），autorotate 都會處理好。

## 7. 字型

Linux 上通常只有 `Noto Sans CJK TC`（Regular + Bold）。沒有更粗的 Black weight，想要更醒目就靠加大字級與加粗描邊（`Outline 6`），不要指望字重。

**macOS 用 `PingFang TC`**（系統內建的蘋方），視覺上比 Noto 好，字重也齊。

⚠️ **字型名稱寫錯不會報錯**，libass 會默默 fallback 成豆腐方塊或英文字型 —— 這種錯很難察覺，成品做完才發現就白費一輪。動工前一定要先確認：

```bash
fc-list | grep -i "CJK\|PingFang"
```

## 8. 檔案大小

1080x1920 / 30fps / CRF 20 的 40 秒影片約 40-60 MB。IG 上傳沒問題（會再壓一次），但：

- Cowork 聊天室的檔案上限是 **30 MB**，超過就傳不了。要在對話裡給預覽，另外壓一版（CRF 26 + `-maxrate 4200k`，約 12-19 MB）
- `device_commit_files` 上限是每檔 20 MB、單次總計 100 MB。成品影片一律**直接在使用者電腦上產生**，不要繞路

## 9. 混音的音量判斷

不要憑感覺調配樂音量。用「音樂版減乾淨版」直接量出配樂的實際音量：

```python
d = music_track - clean_track          # 對齊後相減 = 配樂 + 音效
gap = db(clean_track) - db(d)          # 配樂低於人聲幾 dB
```

目標 **10-12 dB**。低於 6 dB 會蓋住講話；超過 15 dB 等於沒放。

側鏈閃避的參數：`threshold=0.10:ratio=5:attack=10:release=340`。threshold 太低（0.05）加 ratio 太高（9）會讓音樂在整支影片裡幾乎聽不見 —— 口播影片講話幾乎不間斷，壓縮器會一直踩著。

## 10. loudnorm 單次通過的誤差

`loudnorm=I=-16` 單次通過對稀疏內容（例如只有鋼琴、沒有鼓的片子）會低估，實測會落在 -17 LUFS 左右。差 1-1.5 LUFS 可以接受，IG 上傳後也會再正規化一次。真的在意就跑雙通道 loudnorm，但通常不值得。
