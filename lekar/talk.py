"""Модели, которые выдают видео сразу с голосом: Veo 3.1 (fal), Grok Imagine (fal), Sora 2 (OpenAI)."""
import json, os, subprocess, time
from pathlib import Path

from .core import Ledger, cost_of, download, fal_run, load_env, sh, upload

VOICE = ("He speaks Russian in a warm, calm, slightly husky male voice of a 55-year-old man, "
         "at a relaxed pace, clear articulation, lips in sync with the speech.")


def prompt(action, line):
    return (f"{action} The man looks into the camera and says in Russian: \"{line}\" {VOICE} "
            "Only his voice, no music, no background sounds. No subtitles or text on screen. Static camera.")


def veo(ledger, frame, text, dst, seconds=8, fast=True, tag="veo"):
    ep = "fal-ai/veo3.1/fast/image-to-video" if fast else "fal-ai/veo3.1/image-to-video"
    r = fal_run(ledger, ep, dict(prompt=text, image_url=upload(frame), aspect_ratio="9:16", duration=f"{seconds}s",
                                 resolution="720p", generate_audio=True), seconds, tag)
    return download(r["video"]["url"], dst)


def grok(ledger, frame, text, dst, seconds=8, tag="grok"):
    ep = "xai/grok-imagine-video/image-to-video"
    r = fal_run(ledger, ep, dict(prompt=text, image_url=upload(frame), duration=seconds, aspect_ratio="9:16",
                                 resolution="720p"), seconds, tag)
    return download(r["video"]["url"], dst)


def sora(ledger, frame, text, dst, seconds=8, model="sora-2", tag="sora"):
    """OpenAI Videos API: кадр должен совпадать с размером видео."""
    load_env()
    key = os.environ["OPENAI_API_KEY"]
    ref = Path(dst).with_suffix(".ref.jpg")
    sh("ffmpeg", "-y", "-v", "error", "-i", frame, "-vf", "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280",
       "-q:v", "2", ref)
    t0 = time.time()
    out = subprocess.run(["curl", "-sS", "https://api.openai.com/v1/videos", "-H", f"Authorization: Bearer {key}",
                          "-F", f"model={model}", "-F", f"prompt={text}", "-F", f"seconds={seconds}", "-F", "size=720x1280",
                          "-F", f"input_reference=@{ref};type=image/jpeg"], capture_output=True, text=True).stdout
    job = json.loads(out)
    if "id" not in job:
        ledger.call(tag=tag, endpoint=f"openai/{model}", request_id=None, units=0, cost=0, seconds=0, error=str(job)[:500])
        raise RuntimeError(f"sora: {job}")
    print(f"  openai {model} [{tag}] {job['id']}", flush=True)
    while True:
        st = json.loads(subprocess.run(["curl", "-sS", f"https://api.openai.com/v1/videos/{job['id']}",
                                        "-H", f"Authorization: Bearer {key}"], capture_output=True, text=True).stdout)
        if st.get("status") in ("completed", "failed"):
            break
        time.sleep(5)
    ok = st["status"] == "completed"
    ledger.call(tag=tag, endpoint=f"openai/{model}", request_id=job["id"], units=seconds if ok else 0,
                cost=cost_of(f"openai/{model}", seconds) if ok else 0, seconds=round(time.time() - t0, 1),
                **({} if ok else {"error": str(st.get("error"))[:500]}))
    if not ok:
        raise RuntimeError(f"sora: {st.get('error')}")
    subprocess.run(["curl", "-sS", "-o", str(dst), f"https://api.openai.com/v1/videos/{job['id']}/content",
                    "-H", f"Authorization: Bearer {key}"], check=True)
    return Path(dst)


def lineup(clips, dst, h=1280):
    """Клипы рядом, у каждого подпись сверху и свой звук по очереди: сначала все кадры рядом молча
    не годятся для оценки голоса, поэтому склеиваем последовательно: клип1 | клип2 | ..., каждый со своим звуком."""
    parts = []
    for name, path in clips:
        p = Path(dst).with_name(f"_lu_{len(parts)}.mp4")
        label = name.replace(":", r"\:")
        sh("ffmpeg", "-y", "-v", "error", "-i", path, "-vf",
           f"scale=-2:{h},setsar=1,fps=24,drawtext=text='{label}':fontcolor=white:fontsize=44:box=1:boxcolor=black@0.6:boxborderw=12:x=(w-tw)/2:y=40",
           "-af", "aresample=44100,loudnorm=I=-14:TP=-1.5", "-ac", "2", "-c:v", "libx264", "-crf", "20", "-c:a", "aac", "-b:a", "160k", p)
        parts.append(p)
    lst = Path(dst).with_suffix(".txt")
    lst.write_text("".join(f"file '{p.resolve()}'\n" for p in parts))
    sh("ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", "-movflags", "+faststart", dst)
    for p in parts:
        p.unlink()
    lst.unlink()
    return dst
