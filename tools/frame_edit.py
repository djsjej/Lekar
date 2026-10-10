#!/usr/bin/env python3
"""Точечная правка готового кадра: дорисовать/заменить деталь, всё остальное оставить как было.

    python3 tools/frame_edit.py n04            # расчёт
    python3 tools/frame_edit.py n04 --yes      # Nano Banana Pro edit, 0,15 $ за правку

Правки — в samples/<id>/edits.yaml: {номер кадра: {prompt: ..., refs: [пути картинок]}}. Первая картинка запроса —
сам кадр work/<id>/A/frame_N.png, дальше refs. Старый кадр сохраняется рядом как frame_N_before_edit.png.
Выполненная правка помечается в work/<id>/A/edits_done.json, повторно не запускается (кроме --redo N).
"""
import argparse, json, shutil, subprocess, sys, threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import yaml  # noqa: E402
from lekar.core import Ledger, cost_of, download, fal_run, upload  # noqa: E402

EP = "fal-ai/nano-banana-pro/edit"
KEEP = ("Keep the first image exactly the same — same woman, face, pose, hands, clothes, table, objects, room and "
        "light — except for this change: ")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sample"); ap.add_argument("--yes", action="store_true"); ap.add_argument("--redo", nargs="*", default=[])
    a = ap.parse_args()
    edits = yaml.safe_load((ROOT / "samples" / a.sample / "edits.yaml").read_text())
    out = ROOT / "work" / a.sample / "A"
    done_f = out / "edits_done.json"
    done = json.loads(done_f.read_text()) if done_f.exists() else {}
    todo = [k for k in edits if done.get(str(k)) != edits[k]["prompt"] or str(k) in a.redo]
    print(f"  правок: {len(todo)} × 0,15 $ = {cost_of(EP, len(todo)):.2f} $")
    if todo and not a.yes:
        sys.exit("платно: повторить с --yes")
    errs = []

    def one(k):
        e = edits[k]; f = out / f"frame_{e.get('frame', k)}.png"   # frame: — вторая правка того же кадра (ключ 1b)
        try:
            urls = [upload(f)] + [upload(ROOT / r) for r in e.get("refs", [])]
            r = fal_run(Ledger(out / f"ledger_frame_{e.get('frame', k)}.json"), EP,
                        dict(prompt=KEEP + e["prompt"] + " No text on the image.", image_urls=urls, aspect_ratio="9:16",
                             resolution="2K", output_format="png", seed=e.get("seed", 7)), 1, f"{a.sample}: правка кадра {k}")
            shutil.copy(f, out / f"{f.stem}_before_{k}.png")
            download(r["images"][0]["url"], f)
            done[str(k)] = e["prompt"]
        except Exception as ex:
            errs.append((k, str(ex)[:200]))
    th = [threading.Thread(target=one, args=(k,)) for k in todo]
    [t.start() for t in th]; [t.join() for t in th]
    done_f.write_text(json.dumps(done, ensure_ascii=False, indent=1))
    if errs:
        print("  ошибки:", errs)
    for k in todo:
        n = edits[k].get("frame", k)
        subprocess.run([sys.executable, str(ROOT / "tools/remote.py"), "put", str(out / f"frame_{n}.png"),
                        f"work/{a.sample}/A/frame_{n}.png"], capture_output=True)
    print("  готово:", out)


if __name__ == "__main__":
    main()
