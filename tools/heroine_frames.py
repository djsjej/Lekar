#!/usr/bin/env python3
"""Стартовые кадры канала героини: описание кадра из samples/<id>/prompts.yaml → frames + лист персонажа.

    python3 tools/heroine_frames.py n01            # расчёт
    python3 tools/heroine_frames.py n01 --yes [--redo 3 5]

Референсы в каждом запросе: assets/heroine/sheet_1.png (лицо, одежда) и v1.png (кухня). 0,15 $/кадр, 6 параллельно.
Пишет work/<id>/A/frame_N.png и кладёт на сервер.
"""
import argparse, subprocess, sys, threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import yaml  # noqa: E402
from lekar.core import Ledger, cost_of, download, fal_run, upload  # noqa: E402

EP = "fal-ai/nano-banana-pro/edit"
COMMON = (" The woman is the woman from the first image (same face, hair bun, beige knitted cardigan, cream blouse and the dark blue "
          "linen apron with the small embroidered red rowan sprig). The kitchen is the same village house kitchen as in the second "
          "image: white tiled stove, white window frame, wooden table, an icon with a red lampada on the wall in the background, out "
          "of focus. Vertical 9:16, photorealistic smartphone photo, warm natural window daylight, real home look. "
          "No text, captions or logos on the image.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sample"); ap.add_argument("--yes", action="store_true"); ap.add_argument("--redo", nargs="*", default=[])
    a = ap.parse_args()
    pr = yaml.safe_load((ROOT / "samples" / a.sample / "prompts.yaml").read_text())
    frames, common = pr["frames"], pr.get("common", COMMON)   # common: своя одежда/место съёмки на ролик
    out = ROOT / "work" / a.sample / "A"; out.mkdir(parents=True, exist_ok=True)
    todo = [k for k in frames if not (out / f"frame_{k}.png").exists() or str(k) in a.redo]
    print(f"  кадров: {len(todo)} × 0,15 $ = {cost_of(EP, len(todo)):.2f} $")
    if todo and not a.yes:
        sys.exit("платно: повторить с --yes")
    refs = [upload(ROOT / "assets/heroine/sheet_1.png"), upload(ROOT / "assets/heroine/v1.png")]
    sem, errs = threading.Semaphore(6), []

    def one(k):
        with sem:
            try:
                r = fal_run(Ledger(out / f"ledger_frame_{k}.json"), EP,
                            dict(prompt=frames[k] + " " + common.strip(), image_urls=refs, aspect_ratio="9:16", resolution="2K",
                                 output_format="png", seed=40 + int(k)), 1, f"{a.sample}: кадр {k}")
                download(r["images"][0]["url"], out / f"frame_{k}.png")
            except Exception as e:
                errs.append((k, str(e)[:200]))
    th = [threading.Thread(target=one, args=(k,)) for k in todo]
    [t.start() for t in th]; [t.join() for t in th]
    if errs:
        print("  ошибки:", errs)
    for k in frames:
        f = out / f"frame_{k}.png"
        if f.exists():
            subprocess.run([sys.executable, str(ROOT / "tools/remote.py"), "put", str(f), f"work/{a.sample}/A/{f.name}"], capture_output=True)
    print("  готово:", out)


if __name__ == "__main__":
    main()
