"""Кадры, оживление, подгонка, субтитры, сборка, сравнение."""
import re
from pathlib import Path

from .core import FONTS, download, duration, ensure_fonts, fal_run, sh, upload

FPS = 24


# ---------- стартовые кадры ----------

def frames_from_original(original, shots, out_dir):
    out = {}
    for s in shots:
        p = Path(out_dir) / f"frame_{s['id']}.png"
        if p.exists():                  # кадр уже подготовлен (например, переодетый герой) — не трогать
            out[s["id"]] = p; continue
        sh("ffmpeg", "-y", "-v", "error", "-ss", str(s["ref_frame_t"]), "-i", original, "-frames:v", "1", p)
        out[s["id"]] = p
    return out


def frames_generated(ledger, prompts, shots, out_dir, seed=None):
    out_dir = Path(out_dir)
    sheet = out_dir / "character_sheet.png"
    if not sheet.exists():
        r = fal_run(ledger, "fal-ai/nano-banana-pro",
                    dict(prompt=prompts["character_sheet"], aspect_ratio="16:9", resolution="2K",
                         output_format="png", seed=seed), 1, "лист персонажа")
        download(r["images"][0]["url"], sheet)
    out = {}
    for s in shots:
        p = out_dir / f"frame_{s['id']}.png"
        if not p.exists():
            r = fal_run(ledger, "fal-ai/nano-banana-pro/edit",
                        dict(prompt=prompts["frames"][s["id"]], image_urls=[upload(sheet)],
                             aspect_ratio="9:16", resolution="2K", output_format="png", seed=seed),
                        1, f"кадр {s['id']}")
            download(r["images"][0]["url"], p)
        out[s["id"]] = p
    return out


# ---------- оживление ----------

def i2v_seconds(endpoint, need, stretch=0.12):
    """Длина заказа «картинка→видео»: Kling — 5 или 10 с, Grok — целые секунды (лишнее срежется)."""
    import math
    if endpoint.startswith("xai/grok"):
        return max(3, math.ceil(need))
    return 5 if need <= 5 * (1 + stretch) else 10


def animate_talking(ledger, endpoint, frame, slice_wav, prompt, dst, tag):
    sec = duration(slice_wav)
    args = dict(image_url=upload(frame), audio_url=upload(slice_wav))
    if endpoint.startswith("veed/fabric"):
        args["resolution"] = "480p"           # 0,08 $/с; 720p — 0,15 $/с
    else:
        args["prompt"] = prompt
    r = fal_run(ledger, endpoint, args, round(sec, 2), tag)
    return download(r["video"]["url"], dst)


def animate_action(ledger, endpoint, frame, prompt, seconds, dst, tag):
    args = dict(image_url=upload(frame), prompt=prompt, duration=str(seconds))
    if endpoint.startswith("xai/grok"):        # звук Grok не берём: на сборке ляжет наша озвучка
        args = dict(image_url=upload(frame), duration=seconds, aspect_ratio="9:16", resolution="720p",
                    prompt=prompt + " He does not speak, mouth closed or relaxed. No dialogue, no music.")
    r = fal_run(ledger, endpoint, args, seconds, tag)
    return download(r["video"]["url"], dst)


def fit(src, need, dst, w, h, max_stretch=0.12, delogo=None):
    """Видео плана -> ровно need секунд, w×h, 24 к/с, без звука.
    Разница до ±12% — setpts, длиннее — обрезка, короче больше чем на 12% — ошибка."""
    have = duration(src)
    f = need / have
    vf = [f"scale={w}:{h}:force_original_aspect_ratio=increase", f"crop={w}:{h}", "setsar=1"]
    note = "как есть"
    if abs(f - 1) > 0.005 and abs(f - 1) <= max_stretch:
        vf.insert(0, f"setpts={f:.5f}*PTS"); note = f"setpts ×{f:.3f}"
    elif f > 1 + max_stretch:
        raise RuntimeError(f"{src}: видео {have:.2f} с, нужно {need:.2f} с (+{(f-1)*100:.0f}%) — перегенерировать длиннее")
    elif f < 1:
        note = f"обрезка {have:.2f}→{need:.2f} с"
    if delogo:                          # метка генератора в кадре; координаты в сетке 720×1280
        k = w / 720
        x, y, dw, dh = (int(round(v * k)) for v in delogo)
        vf.append(f"delogo=x={x}:y={y}:w={dw}:h={dh}")
    vf += [f"fps={FPS}", f"tpad=stop_mode=clone:stop_duration=1"]
    sh("ffmpeg", "-y", "-v", "error", "-i", src, "-vf", ",".join(vf), "-t", f"{need:.3f}", "-an",
       "-c:v", "libx264", "-preset", "medium", "-crf", "16", "-pix_fmt", "yuv420p", dst)
    return dict(have=round(have, 3), need=round(need, 3), factor=round(f, 4), note=note)


# ---------- субтитры ----------

def sub_token(word):
    w = word.lower()
    return "1" if w == "одну" else w


def _ts(t):
    t = max(t, 0); cs = int(round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def make_ass(words, dst, cfg):
    """Одно слово на экране. Координаты в сетке 720×1280 — libass сам масштабирует под 1080×1920."""
    s = cfg["subs"]
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 720
PlayResY: 1280
ScaledBorderAndShadow: yes
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: W,{s['font']},{s['size']},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,5,0,0,0,204
Style: S,{s['font']},{s['size']},&H{s['shadow_alpha']}000000,&H00000000,&H{s['shadow_alpha']}000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,5,0,0,0,204

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    x, y, off = 360, s["center_y"], s["shadow_offset"]
    lines = []
    for i, w in enumerate(words):
        end = w["end"]
        nxt = words[i + 1]["start"] if i + 1 < len(words) else None
        if nxt is not None and nxt - end < s["bridge_gap"]:
            end = nxt                       # без мигания на стыках слов
        a, b, t = _ts(w["start"]), _ts(end), sub_token(w["word"])
        lines.append(f"Dialogue: 0,{a},{b},S,,0,0,0,,{{\\pos({x + off},{y + off})\\blur{s['shadow_blur']}}}{t}")
        lines.append(f"Dialogue: 1,{a},{b},W,,0,0,0,,{{\\pos({x},{y})}}{t}")
    Path(dst).write_text(head + "\n".join(lines) + "\n")
    return dst


# ---------- сборка ----------

def loudnorm_filter(wav, target=-14.0):
    out = sh("ffmpeg", "-hide_banner", "-i", wav, "-af", f"loudnorm=I={target}:TP=-1.5:LRA=11:print_format=json",
             "-f", "null", "-", quiet=False)
    js = out[out.rindex("{"):out.rindex("}") + 1]
    m = {k: v for k, v in re.findall(r'"(\w+)"\s*:\s*"([^"]+)"', js)}
    return (f"loudnorm=I={target}:TP=-1.5:LRA=11:measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
            f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")


def assemble(shot_videos, voice, ass, dst, w, h):
    ensure_fonts()
    lst = Path(dst).with_suffix(".txt")
    lst.write_text("".join(f"file '{Path(p).resolve()}'\n" for p in shot_videos))
    af = loudnorm_filter(voice) + ",aresample=44100"
    vf = f"scale={w}:{h}:flags=lanczos,ass={ass}:fontsdir={FONTS}"
    sh("ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst, "-i", voice,
       "-map", "0:v", "-map", "1:a", "-vf", vf, "-af", af, "-r", str(FPS),
       "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-pix_fmt", "yuv420p", "-profile:v", "high",
       "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2", "-movflags", "+faststart", "-shortest", dst)
    return dst


def measure_lufs(path):
    out = sh("ffmpeg", "-hide_banner", "-i", path, "-af", "ebur128", "-f", "null", "-", quiet=False)
    m = re.findall(r"I:\s+(-?[\d.]+) LUFS", out)
    return float(m[-1]) if m else None


def side_by_side(original, copy, dst, audio="copy"):
    """Оригинал слева, копия справа, одна дорожка; короткий добивается последним кадром."""
    lo, lc = duration(original), duration(copy)
    L = max(lo, lc)
    fc = (f"[0:v]scale=720:1280,setsar=1,fps={FPS},tpad=stop_mode=clone:stop_duration={L - lo + 0.1:.2f}[a];"
          f"[1:v]scale=720:1280,setsar=1,fps={FPS},tpad=stop_mode=clone:stop_duration={L - lc + 0.1:.2f}[b];"
          f"[a][b]hstack=inputs=2[v]")
    amap = "1:a" if audio == "copy" else "0:a"
    sh("ffmpeg", "-y", "-v", "error", "-i", original, "-i", copy, "-filter_complex", fc,
       "-map", "[v]", "-map", amap, "-t", f"{L:.3f}", "-c:v", "libx264", "-crf", "20", "-preset", "medium",
       "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", dst)
    return dst
