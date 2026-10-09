#!/usr/bin/env python3
"""Стартовые кадры с нашим героем: кадр оригинала → тот же кадр, но герой — наш, в футболке «ХРЕН ИИШНЫЙ».

    python3 tools/hero_frames.py 06                     # расчёт
    python3 tools/hero_frames.py 06 --yes [--redo 2 5]  # Nano Banana Pro edit, 0,15 $ за кадр, 6 параллельно

Берёт work/<id>/src/start_<src>.png для каждого плана из samples/<id>/shots.json (поле "src" — номер
стартового кадра, по умолчанию = id плана), пишет work/<id>/A/frame_<id>.png и кладёт их на сервер.
После — смотреть лист кадров: зашитые субтитры оригинала иногда остаются (тогда --redo этого кадра).
"""
import argparse, json, subprocess, sys, threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lekar.core import Ledger, cost_of, download, fal_run, upload  # noqa: E402

EP = "fal-ai/nano-banana-pro/edit"
SHEET = ROOT / "work/01/hero/character_sheet_v2.png"
PROMPT = ("Recreate the first image as a clean photo: same scene, same camera angle and framing, same pose and hand position, "
          "same objects and any other people exactly as they are, same light. Replace only the main man: he becomes the man from "
          "the second image (keep that face exactly) and instead of his shirt he wears the olive-green T-shirt with the white "
          "inscription \"ХРЕН ИИШНЫЙ\" above the cartoon monkey face, exactly as in the second image. Keep his trousers. "
          "Any white words printed over the first image are not part of the scene: do not draw them. No captions, subtitles or "
          "other text on the image except the T-shirt inscription. Photorealistic, vertical 9:16.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sample"); ap.add_argument("--yes", action="store_true"); ap.add_argument("--redo", nargs="*", default=[])
    a = ap.parse_args()
    shots = json.loads((ROOT / "samples" / a.sample / "shots.json").read_text())
    src, out = ROOT / "work" / a.sample / "src", ROOT / "work" / a.sample / "A"
    out.mkdir(parents=True, exist_ok=True)
    todo = [s for s in shots if not (out / f"frame_{s['id']}.png").exists() or str(s["id"]) in a.redo]
    print(f"  кадров: {len(todo)} × 0,15 $ = {cost_of(EP, len(todo)):.2f} $")
    if todo and not a.yes:
        sys.exit("платно: повторить с --yes")
    sheet, sem, errs = upload(SHEET), threading.Semaphore(6), []

    def one(s):
        with sem:
            try:
                r = fal_run(Ledger(out / f"ledger_frame_{s['id']}.json"), EP,
                            dict(prompt=PROMPT, image_urls=[upload(src / f"start_{s.get('src', s['id'])}.png"), sheet],
                                 aspect_ratio="9:16", resolution="2K", output_format="png", seed=55),
                            1, f"{a.sample}: кадр {s['id']}")
                download(r["images"][0]["url"], out / f"frame_{s['id']}.png")
            except Exception as e:
                errs.append((s["id"], str(e)[:200]))
    th = [threading.Thread(target=one, args=(s,)) for s in todo]
    [t.start() for t in th]; [t.join() for t in th]
    if errs:
        print("  ошибки:", errs)
    for s in shots:
        f = out / f"frame_{s['id']}.png"
        if f.exists():
            subprocess.run([sys.executable, str(ROOT / "tools/remote.py"), "put", str(f), f"work/{a.sample}/A/{f.name}"],
                           capture_output=True)
    print("  готово:", out)


if __name__ == "__main__":
    main()
