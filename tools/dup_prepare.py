#!/usr/bin/env python3
"""Подготовка дубликата чужого ролика: скачать, расшифровать, найти склейки, вытащить стартовые кадры.

    python3 tools/dup_prepare.py 06 --yadisk https://disk.yandex.ru/d/XXXX --path /имя.mp4
    python3 tools/dup_prepare.py 06 --file путь/к/ролику.mp4

Пишет в work/<id>/src/: original.mp4, words.json (whisper, пословно), cuts.json, start_N.png
(кадр через 0,15 с после каждой склейки), sheet.jpg (листы стартовых кадров) — и печатает текст
со временем слов и склейки. Дальше руками: раскадровка и текст в samples/<id>/ (см. docs/PROCESS.md, §6).
Бесплатно (кроме ~0,01 $ за расшифровку).
"""
import argparse, json, re, shutil, subprocess, sys, urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import qa  # noqa: E402


def yadisk(public, path, dst):
    q = urllib.parse.urlencode(dict(public_key=public, path=path))
    href = json.loads(subprocess.run(["curl", "-sS", "-m", "60", f"https://cloud-api.yandex.net/v1/disk/public/resources/download?{q}"],
                                     capture_output=True, text=True).stdout)["href"]
    subprocess.run(["curl", "-sS", "-L", "-m", "600", "-o", str(dst), href], check=True)


def cuts(video, thr=0.08):
    """Порог 0,08: на 0,25–0,3 детектор пропускает склейки внутри одной обстановки (ингредиенты на одном столе)."""
    out = subprocess.run(["ffmpeg", "-i", str(video), "-vf", f"select='gt(scene,{thr})',showinfo", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    return [round(float(x), 3) for x in re.findall(r"pts_time:([\d.]+)", out)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sample"); ap.add_argument("--file"); ap.add_argument("--yadisk"); ap.add_argument("--path")
    a = ap.parse_args()
    d = ROOT / "work" / a.sample / "src"; d.mkdir(parents=True, exist_ok=True)
    v = d / "original.mp4"
    if a.file:
        shutil.copy(a.file, v)
    elif a.yadisk:
        yadisk(a.yadisk, a.path, v)
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=width,height,r_frame_rate",
                            "-of", "csv=p=0", str(v)], capture_output=True, text=True).stdout.split()
    print("ролик:", " ".join(probe))
    wav = d / "audio.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(v), "-vn", "-ac", "1", "-ar", "16000", str(wav)], check=True)
    words, text = qa.whisper(wav); wav.unlink()
    (d / "words.json").write_text(json.dumps(dict(text=text, words=words), ensure_ascii=False, indent=0))
    cs = [0.0] + cuts(v)
    (d / "cuts.json").write_text(json.dumps(cs))
    ins, fc = [], ""
    for i, c in enumerate(cs, 1):
        f = d / f"start_{i}.png"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{c + 0.15:.3f}", "-i", str(v), "-frames:v", "1", str(f)], check=True)
        ins += ["-i", str(f)]
        fc += f"[{i - 1}]scale=180:320,drawtext=text='{i}':fontcolor=yellow:fontsize=40:x=8:y=8[v{i}];"
    fc += "".join(f"[v{i}]" for i in range(1, len(cs) + 1)) + f"hstack={len(cs)}"
    subprocess.run(["ffmpeg", "-y", "-v", "error", *ins, "-filter_complex", fc, str(d / "sheet.jpg")], check=True)
    print("текст:", text)
    print("склейки:", cs)
    for i, c in enumerate(cs, 1):
        end = cs[i] if i < len(cs) else float(probe[-1])
        seg = " ".join(w["word"] for w in words if c - 0.2 <= w["start"] < end - 0.2)
        print(f"  план {i}: {c:.2f}–{end:.2f} ({end - c:.1f} с): {seg}")
    print("лист стартовых кадров:", d / "sheet.jpg")


if __name__ == "__main__":
    main()
