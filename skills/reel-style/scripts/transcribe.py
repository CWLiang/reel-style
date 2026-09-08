#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
transcribe.py — 中文口播逐字稿（含時間碼），輸出 JSON。

    transcribe.py <音檔資料夾> <輸出資料夾>

為什麼不用 Whisper：在 Anthropic 的雲端沙箱裡，HuggingFace 與 OpenAI 的權重 CDN
都被擋（實測 403 / 連線失敗），faster-whisper 與 openai-whisper 都下載不到模型。
但 GitHub Releases 是通的，所以改用 sherpa-onnx + SenseVoice（中文辨識比 Whisper
small/medium 更準，速度也快），VAD 用 silero。兩個權重都掛在 sherpa-onnx 的
GitHub Releases 上。

⚠ 這支只能在**有網路的雲端容器**跑（第一次要下載約 250MB 模型）。
   使用者電腦端的 VM 沒有網路。正確流程是：
     1. device_bash 在使用者電腦上用 ffmpeg 抽出 mp3（很小，25 分鐘約 7MB）
     2. device_stage_files 把 mp3 搬進容器
     3. 在容器裡跑這支
   輸出的時間碼是對原始影片的，可以直接拿來排 segments 與 subs。

首次安裝：
    pip install sherpa-onnx opencc-python-reimplemented soundfile --break-system-packages
"""
import glob, json, os, subprocess, sys, tarfile, urllib.request

MODEL = "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"
BASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models"
HOME = os.path.expanduser("~/.cache/reel-asr")


def ensure_models():
    os.makedirs(HOME, exist_ok=True)
    mdir = f"{HOME}/{MODEL}"
    if not os.path.exists(f"{mdir}/model.int8.onnx"):
        tgz = f"{HOME}/{MODEL}.tar.bz2"
        print("下載 SenseVoice 模型（約 250MB，只有第一次）…", flush=True)
        urllib.request.urlretrieve(f"{BASE}/{MODEL}.tar.bz2", tgz)
        with tarfile.open(tgz, "r:bz2") as t:
            t.extractall(HOME)
        os.remove(tgz)
    vad = f"{HOME}/silero_vad.onnx"
    if not os.path.exists(vad):
        print("下載 silero VAD…", flush=True)
        urllib.request.urlretrieve(f"{BASE}/silero_vad.onnx", vad)
    return mdir, vad


def main(indir, outdir):
    import numpy as np, soundfile as sf, sherpa_onnx
    from opencc import OpenCC
    cc = OpenCC("s2twp")                      # SenseVoice 輸出簡體，轉成台灣繁體
    mdir, vadp = ensure_models()
    rec = sherpa_onnx.OfflineRecognizer.from_sense_voice(
        model=f"{mdir}/model.int8.onnx", tokens=f"{mdir}/tokens.txt",
        use_itn=True, num_threads=max(1, (os.cpu_count() or 2) - 1), language="zh")
    cfg = sherpa_onnx.VadModelConfig()
    cfg.silero_vad.model = vadp
    cfg.silero_vad.threshold = 0.45
    cfg.silero_vad.min_silence_duration = 0.35
    cfg.silero_vad.min_speech_duration = 0.20
    cfg.silero_vad.max_speech_duration = 12.0
    cfg.sample_rate = 16000

    os.makedirs(outdir, exist_ok=True)
    files = sorted(glob.glob(f"{indir}/*.mp3") + glob.glob(f"{indir}/*.wav")
                   + glob.glob(f"{indir}/*.m4a"))
    if not files:
        sys.exit(f"{indir} 裡沒有音檔")
    for f in files:
        b = os.path.splitext(os.path.basename(f))[0]
        dst = f"{outdir}/{b}.json"
        if os.path.exists(dst):
            continue
        wav = f"/tmp/_asr_{b}.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", f, "-ac", "1",
                        "-ar", "16000", wav], check=True)
        audio, sr = sf.read(wav, dtype="float32")
        vad = sherpa_onnx.VoiceActivityDetector(cfg, buffer_size_in_seconds=900)
        segs = []; i = 0

        def drain():
            # ⚠️ sherpa-onnx ≥1.13 的 vad.front 回傳的是緩衝區「參考」，pop() 之後即失效。
            # 舊寫法 `sp = vad.front; vad.pop(); segs.append((..., sp.samples))`
            # 會拿到長度 0 的樣本 → 辨識出空字串 → 被 `if txt` 濾掉 → 整份逐字稿 0 句，
            # 而且完全不報錯。必須在 pop() 之前把 samples 複製出來。
            while not vad.empty():
                sp = vad.front
                st = sp.start / sr
                sm = np.array(sp.samples, dtype="float32", copy=True)
                vad.pop()
                if len(sm):
                    segs.append((st, sm))

        while i < len(audio):
            vad.accept_waveform(audio[i:i+512]); i += 512
            drain()
        vad.flush()
        drain()
        res = []
        for st, samples in segs:
            s = rec.create_stream(); s.accept_waveform(sr, samples); rec.decode_stream(s)
            txt = s.result.text.strip()
            if txt:
                res.append({"s": round(st, 2), "e": round(st + len(samples)/sr, 2),
                            "t": cc.convert(txt)})
        json.dump(res, open(dst, "w"), ensure_ascii=False, indent=0)
        os.remove(wav)
        print(f"{b}: {len(res)} 句", flush=True)
    print("完成。逐字稿有錯字是正常的 —— 人名、專有名詞、數字都要人工校對再寫進 plan。")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
