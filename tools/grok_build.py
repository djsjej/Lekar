#!/usr/bin/env python3
"""Ролик целиком на Grok: каждый план — клип Grok со своей речью (в кадре или за кадром).

    python3 tools/grok_build.py 01 --shots 1 2 3 [--redo 3] [--yes]

1. для каждого плана: стартовый кадр work/<образец>/A/frame_N.png + текст плана -> клип Grok со звуком;
   длина клипа — длина плана в оригинале, округлённая вверх (Grok берёт целые секунды);
2. расшифровка клипа (OpenAI whisper, пословно): проверка, что сказан ровно текст плана, и тайминги для субтитров;
3. клипы, план и тайминги -> на сервер, сборка шагом `run.py assemble_g` (монтаж только на Railway).
Генерация и расшифровка идут отсюда: это запросы к fal и OpenAI, ключ OpenAI на сервер не переносим.
"""
import argparse, difflib, json, math, os, re, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import yaml  # noqa: E402
from lekar import talk  # noqa: E402
from lekar.core import Ledger, cost_of, download, duration, fal_run, load_env, upload  # noqa: E402

EP = "xai/grok-imagine-video/image-to-video"
VOICE = talk.VOICE


def prompt(shot, action):
    line = shot["text"]
    if shot.get("speech", "on") == "voiceover":
        return (f"{action} He does not speak on camera, his mouth stays closed. An off-screen voice-over says in Russian: "
                f"\"{line}\" The voice-over is the same man: warm, calm, slightly husky male voice of a 55-year-old, "
                "natural brisk pace of about 2.3 words per second. No music, no other sounds. No text on screen. Static camera.")
    return (f"{action} While doing this he talks to the camera and says in Russian: \"{line}\" {VOICE} "
            "Speak at a natural brisk pace of about 2.3 words per second so the whole line fits the clip. "
            "Only his voice, no music, no background sounds. No subtitles or text on screen. Static camera.")


def whisper(path):
    load_env()
    wav = path.with_suffix(".16k.wav")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000", str(wav)], check=True)
    out = subprocess.run(["curl", "-sS", "-m", "180", "https://api.openai.com/v1/audio/transcriptions",
                          "-H", f"Authorization: Bearer {os.environ['OPENAI_API_KEY']}", "-F", "model=whisper-1",
                          "-F", "language=ru", "-F", f"file=@{wav}", "-F", "response_format=verbose_json",
                          "-F", "timestamp_granularities[]=word"], capture_output=True, text=True).stdout
    d = json.loads(out); wav.unlink()
    return d["text"], [dict(word=w["word"], start=round(w["start"], 3), end=round(w["end"], 3)) for w in d["words"]]


def speech_onset(path, thr=0.06):
    """Начало речи по громкости: whisper ставит первому слову после тишины время 0."""
    import numpy as np
    x = np.frombuffer(subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", "8000",
                                      "-f", "s16le", "-"], capture_output=True).stdout, np.int16).astype(float)
    r = np.array([np.sqrt((x[i:i + 160] ** 2).mean()) for i in range(0, len(x) - 160, 160)])
    on = np.nonzero(r > thr * r.max())[0]
    return round(on[0] * 0.02, 3) if len(on) else 0.0


def norm(w):
    return re.sub(r"[^\w]", "", w.lower().replace("ё", "е")).replace("одну", "1")


def script_words(shot, heard):
    """Слова сценария с таймингами из расшифровки: субтитры пишутся как в тексте, время — как сказано."""
    want = re.findall(r"[\w\-]+", shot["text"])
    sm = difflib.SequenceMatcher(a=[norm(w) for w in want], b=[norm(h["word"]) for h in heard], autojunk=False)
    out, issues = [], []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            out += [dict(word=want[i], start=heard[j]["start"], end=heard[j]["end"]) for i, j in zip(range(i1, i2), range(j1, j2))]
            continue
        if op == "replace" and i2 - i1 == j2 - j1:          # похожее слово (падеж, ё) — берём время из расшифровки
            out += [dict(word=want[i], start=heard[j]["start"], end=heard[j]["end"]) for i, j in zip(range(i1, i2), range(j1, j2))]
        issues.append(f"ждали «{' '.join(want[i1:i2]) or '—'}», слышно «{' '.join(h['word'] for h in heard[j1:j2]) or '—'}»")
        if op == "delete" or (op == "replace" and i2 - i1 != j2 - j1):   # не сказано — субтитр по соседям
            t = heard[j1]["start"] if j1 < len(heard) else (heard[-1]["end"] if heard else 0)
            out += [dict(word=want[i], start=t, end=t + 0.3) for i in range(i1, i2)]
    out.sort(key=lambda w: w["start"])
    return out, issues


def remote(*args):
    r = subprocess.run([sys.executable, str(ROOT / "tools/remote.py"), *args], capture_output=True, text=True)
    if r.returncode:
        sys.exit(r.stdout + r.stderr)
    return r.stdout


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sample"); ap.add_argument("--shots", type=int, nargs="*")
    ap.add_argument("--redo", type=int, nargs="*", default=[]); ap.add_argument("--yes", action="store_true")
    a = ap.parse_args()
    sdir = ROOT / "samples" / a.sample
    shots = json.loads((sdir / "shots.json").read_text())
    prompts = yaml.safe_load((sdir / "prompts.yaml").read_text())
    part = [s for s in shots if not a.shots or s["id"] in a.shots]
    out = ROOT / "work" / a.sample / "G"; out.mkdir(parents=True, exist_ok=True)
    frames = ROOT / "work" / a.sample / "A"
    ledger = Ledger(out / "ledger.json")

    # единицы генерации: план целиком или его части (длинный план > 15 с — несколько клипов подряд,
    # каждый следующий стартует с последнего кадра предыдущего, чтобы поза не прыгала на стыке)
    units = []
    for s in part:
        if s.get("grok_parts"):
            for k, t in enumerate(s["grok_parts"], 1):
                units.append(dict(key=f"{s['id']}.{k}", shot=s, text=t, orig=None, chain=k > 1,
                                  sec=min(15, math.ceil(len(re.findall(r"\w+", t)) / 2.4 + 0.3))))
        else:
            units.append(dict(key=str(s["id"]), shot=s, text=s["text"], orig=round(s["orig_end"] - s["orig_start"], 3),
                              chain=False, sec=math.ceil(s["orig_end"] - s["orig_start"])))
    redo = {str(x) for x in a.redo}
    todo = [u for u in units if not (out / f"grok_{u['key']}.mp4").exists()
            or u["key"] in redo or u["key"].split(".")[0] in redo]
    total = sum(cost_of(EP, u["sec"]) for u in todo)
    for u in todo:
        print(f"  план {u['key']}: Grok {u['sec']} с — {cost_of(EP, u['sec']):.2f} $")
    print(f"  итого {total:.2f} $")
    if todo and not a.yes:
        sys.exit("платно: повторить с --yes")

    prev = None
    for u in units:
        clip = out / f"grok_{u['key']}.mp4"
        if u in todo:
            s = u["shot"]
            if u["chain"] and prev is not None:
                frame = out / f"start_{u['key']}.png"
                subprocess.run(["ffmpeg", "-y", "-v", "error", "-sseof", "-0.08", "-i", str(prev), "-frames:v", "1",
                                "-update", "1", str(frame)], check=True)
            else:
                frame = frames / f"frame_{s['id']}.png"
                if not frame.exists():
                    remote("get", f"work/{a.sample}/A/frame_{s['id']}.png", str(frame))
            act = prompts["grok"][s["id"]] + (" He continues the same speech from the previous moment." if u["chain"] else "")
            r = fal_run(ledger, EP, dict(image_url=upload(frame), prompt=prompt(dict(s, text=u["text"]), act),
                                         duration=u["sec"], aspect_ratio="9:16", resolution="720p"),
                        u["sec"], f"G: план {u['key']} Grok {u['sec']} с")
            download(r["video"]["url"], clip)
        prev = clip

    plan, report = [], []
    for u in units:
        clip = out / f"grok_{u['key']}.mp4"
        text, heard = whisper(clip)
        if heard:                                   # первое слово — с реального начала речи
            on = speech_onset(clip)
            if on > heard[0]["start"] + 0.1 and on < heard[0]["end"]:
                heard[0]["start"] = on
        words, issues = script_words(dict(text=u["text"]), heard)
        speech_end = heard[-1]["end"] if heard else duration(clip)
        plan.append(dict(id=u["key"], shot=u["shot"]["id"], file=clip.name, clip=round(duration(clip), 3),
                         speech_end=speech_end, orig=u["orig"], delogo=u["shot"].get("delogo"), words=words))
        report.append(f"план {u['key']}: «{text}»" + ("".join(f"\n    ! {x}" for x in issues) if issues else "  — текст совпал"))
    (out / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1))
    print("\n".join(report))
    for p in plan:
        remote("put", str(out / p["file"]), f"work/{a.sample}/G/{p['file']}")
    remote("put", str(out / "plan.json"), f"work/{a.sample}/G/plan.json")
    print("  на сервере: python3 tools/remote.py run", a.sample, "assemble_g")


if __name__ == "__main__":
    main()
