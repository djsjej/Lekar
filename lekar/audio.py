"""Озвучка, тайминги слов, нарезка по планам."""
import json, re, wave
from pathlib import Path

import numpy as np

from .core import download, fal_run, sh

WORD = re.compile(r"[\w\-]+", re.U)


def tts(ledger, cfg, text, out_dir, voice=None, tag="tts"):
    """Весь текст одним куском. Пишет voice.wav (44,1 кГц стерео) и сырые тайминги."""
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    args = dict(text=text, voice=voice or cfg["voice"], timestamps=True, language_code="ru",
                **cfg.get("voice_settings", {}))
    r = fal_run(ledger, cfg["tts"], args, len(text) / 1000, tag)
    src = download(r["audio"]["url"], out_dir / ("voice_src" + Path(r["audio"]["url"]).suffix))
    sh("ffmpeg", "-y", "-v", "error", "-i", src, "-ar", "44100", "-ac", "2", out_dir / "voice.wav")
    (out_dir / "tts_raw.json").write_text(json.dumps(r.get("timestamps"), ensure_ascii=False))
    return out_dir / "voice.wav"


def _from_chars(chars, starts, ends):
    words, cur, s0, e0 = [], "", None, None
    for c, s, e in zip(chars, starts, ends):
        if WORD.match(c):
            if not cur:
                s0 = s
            cur += c; e0 = e
        elif cur:
            words.append(dict(word=cur, start=s0, end=e0)); cur = ""
    if cur:
        words.append(dict(word=cur, start=s0, end=e0))
    return words


def normalize_timestamps(raw):
    """Разные формы ответа синтеза -> [{word,start,end}] только для слов."""
    if not raw:
        return None
    if isinstance(raw, dict) and "characters" in raw:
        return _from_chars(raw["characters"], raw["character_start_times_seconds"], raw["character_end_times_seconds"])
    if isinstance(raw, list) and raw and isinstance(raw[0], dict) and "characters" in raw[0]:
        out = []
        for chunk in raw:
            out += _from_chars(chunk["characters"], chunk["character_start_times_seconds"], chunk["character_end_times_seconds"])
        return out
    words = []
    for w in raw:
        t = w.get("text", w.get("word", ""))
        s = w.get("start", w.get("start_time"))
        e = w.get("end", w.get("end_time"))
        for i, tok in enumerate(WORD.findall(t)):
            words.append(dict(word=tok, start=s, end=e))
    return words


def whisper_words(wav):
    from faster_whisper import WhisperModel
    m = WhisperModel("small", compute_type="int8")
    segs, _ = m.transcribe(str(wav), language="ru", word_timestamps=True)
    return [dict(word=t, start=w.start, end=w.end)
            for s in segs for w in s.words for t in WORD.findall(w.word)]


def attach_script(words, shots):
    """Сопоставить слова синтеза со словами сценария (порядок один и тот же) и пометить план."""
    script = [(tok, s["id"]) for s in shots for tok in WORD.findall(s["text"])]
    if len(script) != len(words):
        raise SystemExit(f"слов в сценарии {len(script)}, в таймингах {len(words)} — выровнять вручную")
    out = []
    for (tok, sid), w in zip(script, words):
        if tok.lower().replace("ё", "е") != w["word"].lower().replace("ё", "е"):
            print(f"  внимание: '{tok}' против '{w['word']}'")
        out.append(dict(word=tok, start=round(w["start"], 3), end=round(w["end"], 3), shot=sid))
    return out


def read_mono(wav):
    with wave.open(str(wav)) as f:
        sr, ch, n = f.getframerate(), f.getnchannels(), f.getnframes()
        a = np.frombuffer(f.readframes(n), dtype=np.int16).astype(np.float32)
    return a.reshape(-1, ch).mean(axis=1), sr


def cut_points(wav, words):
    """Граница плана — самая тихая точка в паузе между последним словом плана и первым следующего."""
    a, sr = read_mono(wav)
    win = int(sr * 0.02)
    cuts = []
    for prev, nxt in zip(words, words[1:]):
        if prev["shot"] == nxt["shot"]:
            continue
        lo, hi = int(prev["end"] * sr), int(nxt["start"] * sr)
        if hi - lo < win * 2:
            cuts.append(round((prev["end"] + nxt["start"]) / 2, 3)); continue
        seg = a[lo:hi]
        rms = [np.sqrt(np.mean(seg[i:i + win] ** 2)) for i in range(0, len(seg) - win, win // 2)]
        i = int(np.argmin(rms)) * (win // 2) + win // 2
        cuts.append(round((lo + i) / sr, 3))
    return cuts


def slice_audio(wav, cuts, out_dir):
    total = float(sh("ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", wav))
    edges = [0.0] + cuts + [total]
    paths = []
    for i, (a, b) in enumerate(zip(edges, edges[1:]), 1):
        p = Path(out_dir) / f"slice_{i}.wav"
        sh("ffmpeg", "-y", "-v", "error", "-i", wav, "-ss", f"{a:.3f}", "-to", f"{b:.3f}", "-c:a", "pcm_s16le", p)
        paths.append(dict(id=i, start=a, end=b, dur=round(b - a, 3), path=str(p)))
    return paths


def match_cuts(wav, words, slices, shots, out_dir, pad=(0.15, 0.8), tempo=(0.9, 1.1)):
    """Подогнать куски под длины планов оригинала: сперва пауза в конце куска, остаток — atempo.
    Пишет новый voice.wav, сдвинутые тайминги и нарезку. Возвращает (words, slices, заметки)."""
    out_dir = Path(out_dir)
    new_words, new_slices, notes, parts, t = [], [], [], [], 0.0
    for sl, sh_ in zip(slices, shots):
        ws = [w for w in words if w["shot"] == sh_["id"]]
        speech = min(ws[-1]["end"] + 0.12, sl["end"]) - sl["start"]
        lead = ws[0]["start"] - sl["start"]           # тишина перед первым словом остаётся как есть
        target = sh_["orig_end"] - sh_["orig_start"]
        want_pad = target - speech
        p = min(max(want_pad, pad[0]), pad[1])
        r = speech / (target - p)
        r = min(max(r, tempo[0]), tempo[1])
        p = max(target - speech / r, 0.05)
        piece = out_dir / f"m_{sl['id']}.wav"
        af = (f"atempo={r:.5f}," if abs(r - 1) > 0.002 else "") + f"apad=pad_dur={p:.3f}"
        sh("ffmpeg", "-y", "-v", "error", "-i", wav, "-ss", f"{sl['start']:.3f}", "-t", f"{speech:.3f}",
           "-af", af, "-t", f"{speech / r + p:.3f}", "-c:a", "pcm_s16le", piece)
        dur = speech / r + p
        for w in ws:
            new_words.append(dict(w, start=round(t + (w["start"] - sl["start"]) / r, 3),
                                  end=round(t + (w["end"] - sl["start"]) / r, 3)))
        new_slices.append(dict(id=sl["id"], start=round(t, 3), end=round(t + dur, 3), dur=round(dur, 3)))
        notes.append(dict(id=sl["id"], speech=round(speech, 2), target=round(target, 2), tempo=round(r, 4),
                          pad=round(p, 2), lead=round(lead, 2), result=round(dur, 2)))
        parts.append(piece); t += dur
    lst = out_dir / "m_list.txt"
    lst.write_text("".join(f"file '{p.resolve()}'\n" for p in parts))
    sh("ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst, "-c:a", "pcm_s16le", out_dir / "voice.wav")
    for s in new_slices:
        p = out_dir / f"slice_{s['id']}.wav"
        sh("ffmpeg", "-y", "-v", "error", "-i", out_dir / "voice.wav", "-ss", f"{s['start']:.3f}", "-to", f"{s['end']:.3f}",
           "-c:a", "pcm_s16le", p)
        s["path"] = str(p)
    for p in parts:
        p.unlink()
    lst.unlink()
    return new_words, new_slices, notes
