#!/bin/bash
# doctor.sh — 環境自我檢查。全綠就直接進流程，不要問使用者任何事。
# 每一項失敗都印出「可以直接執行的修法」，讓 Claude 代跑、使用者只要按允許。
# 退出碼：0 = 全部就緒，1 = 有缺，2 = 缺到不能跑。

ok=0; warn=0; fail=0
g="\033[32m✔\033[0m"; y="\033[33m!\033[0m"; r="\033[31m✗\033[0m"

say_fix() { printf "    → 修法：%s\n" "$1"; }

echo "── reel-style 環境檢查 ──"

PY_PROBE=""
for cand in "$REEL_PYTHON" "$(command -v python3)" /usr/bin/python3; do
  [ -x "$cand" ] && { PY_PROBE="$cand"; break; }
done


# 1. ffmpeg 本體
if command -v ffmpeg >/dev/null 2>&1; then
  printf "$g ffmpeg  %s\n" "$(ffmpeg -version 2>/dev/null | head -1 | cut -d' ' -f1-3)"
  ok=$((ok+1))
else
  printf "$r ffmpeg 沒安裝\n"
  say_fix "brew install ffmpeg-full   # 不要用 brew install ffmpeg，那個精簡版沒有 libass"
  fail=$((fail+1))
fi

# 2. libass —— 沒有它就完全不能上字卡，這是硬需求
if ffmpeg -hide_banner -filters 2>/dev/null | grep -q " ass "; then
  printf "$g libass（字卡渲染）\n"; ok=$((ok+1))
else
  printf "$r libass 缺失 —— 完全無法上字卡\n"
  say_fix "brew install ffmpeg-full && ln -sf \$(brew --prefix ffmpeg-full)/bin/ffmpeg ~/.local/bin/ffmpeg"
  printf "      （ffmpeg-full 是預編譯 bottle，不需要編譯器；它是 keg-only 所以要自己 symlink）\n"
  fail=$((fail+1))
fi

# 3. zscale —— iPhone HDR 素材要靠它 tone map，否則畫面會刷白
if ffmpeg -hide_banner -filters 2>/dev/null | grep -q " zscale "; then
  printf "$g zscale（HDR 轉換）\n"; ok=$((ok+1))
else
  printf "$y zscale 缺失 —— iPhone HDR 素材會刷白，SDR 素材不受影響\n"
  say_fix "brew install ffmpeg-full"
  warn=$((warn+1))
fi

# 4. 中文字型
# 不能只靠 fc-list —— 那是 brew 裝的命令列工具，libass 用的是 fontconfig 函式庫，
# 兩者是不同的東西。沒 brew 的機器上 fc-list 不存在，但中文照樣渲染得出來。
# 所以改成「真的渲染一次中文，看有沒有畫出東西」。
font=$(fc-list 2>/dev/null | grep -ioE "PingFang TC|Noto Sans TC|Noto Sans CJK TC|Microsoft JhengHei" | head -1)
if command -v ffmpeg >/dev/null 2>&1; then
  tmp=$(mktemp -d)
  cat > "$tmp/t.ass" <<'ASS'
[Script Info]
ScriptType: v4.00+
PlayResX: 400
PlayResY: 120
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, Bold, Alignment, Encoding
Style: D,PingFang TC,56,&H00000000&,1,5,1
[Events]
Format: Layer, Start, End, Style, Text
Dialogue: 0,0:00:00.00,0:00:01.00,D,{\an5\pos(200,60)}中文字型測試
ASS
  ffmpeg -v error -y -f lavfi -i color=white:s=400x120:d=1 -vf "ass=$tmp/t.ass" \
         -frames:v 1 -pix_fmt gray -f rawvideo "$tmp/t.raw" 2>/dev/null
  ratio=$($PY_PROBE - "$tmp/t.raw" 2>/dev/null <<'PYEOF'
import sys
try:
    d = open(sys.argv[1], "rb").read()
    print(int(100 * sum(1 for v in d if v < 100) / len(d)) if d else 0)
except Exception:
    print(-1)
PYEOF
)
  rm -rf "$tmp"
  if [ "${ratio:-0}" -ge 2 ] 2>/dev/null; then
    printf "$g 中文可渲染（實際畫過一次，深色像素 %s%%）%s\n" "$ratio" \
           "${font:+　字型：$font}"
    ok=$((ok+1))
  else
    printf "$r 中文渲染不出來 —— 字幕會是空白或豆腐方塊\n"
    say_fix "macOS/Windows 通常內建中文字型；若真的缺，裝 brew install --cask font-noto-sans-cjk-tc"
    fail=$((fail+1))
  fi
else
  printf "$y 中文字型 —— 沒有 ffmpeg，無法實測（先裝 ffmpeg 再跑一次）\n"
  warn=$((warn+1))
fi

# 5. Python（只需要直譯器本身 —— 這條管線刻意不依賴任何第三方套件）
PY=""
for cand in "$REEL_PYTHON" "$(command -v python3)" /usr/bin/python3; do
  [ -x "$cand" ] || continue
  PY="$cand"; break
done
if [ -n "$PY" ]; then
  printf "$g python3  %s（%s）\n" "$PY" "$($PY -V 2>&1 | cut -d' ' -f2)"
  printf "    → 跑腳本時用這一支：%s\n" "$PY"
  ok=$((ok+1))
else
  printf "$r 找不到 python3\n"
  say_fix "macOS 會在第一次呼叫 python3 時跳出 Xcode Command Line Tools 的安裝視窗，點「安裝」即可"
  fail=$((fail+1))
fi

# 6. 語音辨識（選配：原片已有燒錄字幕時用不到）
if $PY -c "import sherpa_onnx, opencc, soundfile" >/dev/null 2>&1; then
  printf "$g 語音辨識（sherpa-onnx + SenseVoice）\n"; ok=$((ok+1))
else
  printf "$y 語音辨識未裝 —— 原片已有燒錄字幕的話用不到，可以先跳過\n"
  say_fix "python3 -m pip install sherpa-onnx opencc-python-reimplemented soundfile --break-system-packages"
  warn=$((warn+1))
fi

echo "──"
if [ $fail -gt 0 ]; then
  printf "$r 有 %d 項必要元件缺失。上面每一項都附了可直接執行的指令。\n" "$fail"
  exit 2
elif [ $warn -gt 0 ]; then
  printf "$y 就緒（%d 項選配未裝，不影響主流程）\n" "$warn"
  exit 1
else
  printf "$g 全部就緒，直接開始。\n"
  exit 0
fi
