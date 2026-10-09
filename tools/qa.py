#!/usr/bin/env python3
"""Проверка готовой копии: что реально звучит и что показывают субтитры.

    python3 tools/qa.py 01 A [--shots 1 2 3]

Берёт с сервера ролик, words.json и subs.ass, расшифровывает звук (OpenAI whisper, пословно)
и сверяет со сценарием:
  - лишние и повторённые слова, пропуски — по выравниванию последовательностей;
  - сдвиг каждого слова: где оно звучит и где стоит в таймингах/субтитрах (порог 0,25 с);
  - повтор звука на склейке — по корреляции окон по обе стороны каждой склейки;
  - листы кадров по планам для просмотра глазами.
Пишет work/<образец>/<режим>/qa<часть>.md и печатает итог.
"""
import argparse, difflib, json, os, re, subprocess, sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lekar.core import load_env  # noqa: E402

TOL = 0.25


def remote(*args):
    r = subprocess.run([sys.executable, str(ROOT / "tools/remote.py"), *args], capture_output=True, text=True)
    if r.returncode:
        sys.exit(r.stdout + r.stderr)


def fetch(rel, dst):
    dst = Path(dst); dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    remote("get", rel, str(dst))
    return dst


def norm(w):
    return re.sub(r"[^\w]", "", w.lower().replace("ё", "е"))


def whisper(wav):
    load_env()
    out = subprocess.run(["curl", "-sS", "-m", "180", "https://api.openai.com/v1/audio/transcriptions",
                          "-H", f"Authorization: Bearer {os.environ['OPENAI_API_KEY']}",
                          "-F", "model=whisper-1", "-F", "language=ru", "-F", f"file=@{wav}",
                          "-F", "response_format=verbose_json", "-F", "timestamp_granularities[]=word"],
                         capture_output=True, text=True).stdout
    d = json.loads(out)
    return [dict(word=x["word"], start=x["start"], end=x["end"]) for x in d["words"]], d["text"]


def pcm(f, a=None, t=None, sr=8000):
    cmd = ["ffmpeg", "-v", "error"] + (["-ss", f"{a:.3f}"] if a is not None else []) + (["-t", f"{t:.3f}"] if t else [])
    cmd += ["-i", str(f), "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"]
    return np.frombuffer(subprocess.run(cmd, capture_output=True).stdout, np.int16).astype(float)


def seam_repeat(video, cut, sr=8000, win=0.35):
    """Повтор на склейке: звук сразу после склейки похож на звук до неё."""
    before = pcm(video, max(cut - 1.2, 0), 1.2)
    after = pcm(video, cut, 1.0)
    best = 0.0
    n = int(win * sr)
    for off in range(0, len(after) - n, n // 2):
        seg = after[off:off + n]
        if np.sqrt((seg ** 2).mean()) < 300:
            continue
        c = np.correlate(before, seg, "valid")
        e = np.sqrt(np.convolve(before ** 2, np.ones(n), "valid")) * np.linalg.norm(seg) + 1e-6
        best = max(best, float((c / e).max()))
    return best


def ass_events(path):
    ev = []
    for line in Path(path).read_text().splitlines():
        if line.startswith("Dialogue: 1,"):
            p = line.split(",", 9)
            t = lambda s: int(s[0]) * 3600 + int(s[2:4]) * 60 + float(s[5:])
            ev.append(dict(start=t(p[1]), end=t(p[2]), text=re.sub(r"\{[^}]*\}", "", p[9])))
    return ev


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sample"); ap.add_argument("mode", nargs="?", default="A")
    ap.add_argument("--shots", type=int, nargs="*")
    a = ap.parse_args()
    shots = json.loads((ROOT / "samples" / a.sample / "shots.json").read_text())
    part = [s for s in shots if not a.shots or s["id"] in a.shots]
    suffix = f"_p{part[0]['id']}-{part[-1]['id']}" if a.shots else ""
    tag = f"replica_{a.sample}" + ("" if a.mode == "A" else "_B") + suffix
    loc = ROOT / "work" / a.sample / a.mode / "qa"
    video = fetch(f"work/{a.sample}/{a.mode}/{tag}.mp4", loc / f"{tag}.mp4")
    words = json.loads(fetch(f"work/{a.sample}/audio/words.json", loc / "words.json").read_text())
    slices = json.loads(fetch(f"work/{a.sample}/audio/slices.json", loc / "slices.json").read_text())
    subs = ass_events(fetch(f"work/{a.sample}/{a.mode}/subs{suffix}.ass", loc / "subs.ass"))

    ids = {s["id"] for s in part}
    sl = {s["id"]: s for s in slices}
    t0 = sl[part[0]["id"]]["start"]
    exp = [dict(w, start=w["start"] - t0, end=w["end"] - t0) for w in words if w["shot"] in ids]
    wav = loc / "audio.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", str(wav)], check=True)
    heard, text = whisper(wav)

    L = [f"# QA: {tag}", "", f"Расшифровка: {text}", ""]
    problems = []

    # 1. слова: лишние, пропуски, повторы
    sm = difflib.SequenceMatcher(a=[norm(w["word"]) for w in exp], b=[norm(w["word"]) for w in heard], autojunk=False)
    pairs = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            pairs += list(zip(range(i1, i2), range(j1, j2)))
        elif op in ("replace", "delete", "insert"):
            want = " ".join(w["word"] for w in exp[i1:i2]); got = " ".join(w["word"] for w in heard[j1:j2])
            # whisper пишет «одну» цифрой и путает ё/е — это не ошибка озвучки
            if op == "replace" and norm(want).replace("одну", "1") == norm(got).replace("одну", "1"):
                continue
            t = heard[j1]["start"] if j1 < len(heard) else (heard[-1]["end"] if heard else 0)
            problems.append(f"текст около {t:.2f} с: ждали «{want or '—'}», слышно «{got or '—'}»")

    # 2. сдвиг слов относительно таймингов (по ним стоят субтитры)
    shifts = []
    for i, j in pairs:
        d = heard[j]["start"] - exp[i]["start"]
        shifts.append(d)
        if abs(d) > TOL:
            problems.append(f"слово «{exp[i]['word']}»: звучит в {heard[j]['start']:.2f} с, по таймингам {exp[i]['start']:.2f} с ({d:+.2f})")
    if shifts:
        L += [f"Сдвиг слов «звук − тайминг»: медиана {np.median(shifts):+.3f} с, худший {max(shifts, key=abs):+.3f} с "
              f"(whisper сам ошибается на ~0,1–0,2 с).", ""]

    # 3. субтитры = тайминги слов
    for w, e in zip(exp, subs):
        want = "1" if norm(w["word"]) == "одну" else w["word"].lower()
        if e["text"] != want or abs(e["start"] - w["start"]) > 0.05:
            problems.append(f"субтитр «{e['text']}» в {e['start']:.2f} с, ждали «{want}» в {w['start']:.2f} с")
    if len(subs) != len(exp):
        problems.append(f"субтитров {len(subs)}, слов {len(exp)}")

    # 4. повтор звука на склейках
    for s in part[:-1]:
        cut = sl[s["id"]]["end"] - t0
        r = seam_repeat(video, cut)
        L.append(f"Склейка {s['id']}→{s['id'] + 1} ({cut:.2f} с): сходство звука до/после {r:.2f}")
        if r > 0.6:                  # на исправной склейке ~0,4; повтор хвоста слова давал 0,69
            problems.append(f"склейка {s['id']}→{s['id'] + 1}: звук после склейки повторяет звук до неё ({r:.2f})")
    L.append("")

    # 5. листы кадров по планам — смотреть глазами
    for s in part:
        a0, a1 = sl[s["id"]]["start"] - t0, sl[s["id"]]["end"] - t0
        out = loc / f"sheet_{s['id']}.jpg"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{a0:.2f}", "-t", f"{a1 - a0:.2f}", "-i", str(video),
                        "-vf", "fps=2,scale=240:-2,tile=6x4", "-frames:v", "1", str(out)], check=True)
        L.append(f"Лист кадров плана {s['id']}: {out.relative_to(ROOT)}")

    L += ["", "## Проблемы", ""] + ([f"- {p}" for p in problems] or ["- не найдено"])
    (loc / f"qa{suffix}.md").write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
