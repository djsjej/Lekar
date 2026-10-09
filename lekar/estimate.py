"""Расчёт стоимости прогона до любой генерации."""
import re

from .core import PRICES, cost_of

WPS = 2.0          # темп голоса из задания, слов в секунду
PAUSE = 0.4        # средняя пауза между фразами


def predict_seconds(text):
    words = len(re.findall(r"\w+", text))
    pauses = len(re.findall(r"[.!?,—]", text))
    return words / WPS + pauses * PAUSE * 0.5


def i2v_duration(seconds, stretch=0.12):
    """Kling i2v умеет 5 или 10 с; берём 5, если подгонка укладывается в ±12%."""
    return 5 if seconds <= 5 * (1 + stretch) else 10


def plan(shots, text, mode, cfg, durations=None):
    """Список платных вызовов. durations — реальные длины кусков аудио, если они уже есть."""
    rows = []
    rows.append(dict(step="озвучка", endpoint=cfg["tts"], units=len(text) / 1000,
                     what=f"{len(text)} символов, весь текст одним куском"))
    if mode == "B":
        rows.append(dict(step="кадры", endpoint="fal-ai/nano-banana-pro", units=1, what="лист персонажа"))
        rows.append(dict(step="кадры", endpoint="fal-ai/nano-banana-pro/edit", units=len(shots),
                         what=f"{len(shots)} стартовых кадра по листу персонажа"))
    for s in shots:
        sec = ((durations or {}).get(s["id"])
               or (s["orig_end"] - s["orig_start"] if "orig_end" in s else predict_seconds(s["text"])))
        if s["kind"] == "talking":
            rows.append(dict(step=f"план {s['id']}", endpoint=cfg["avatar"], units=round(sec, 1),
                             what=f"аватар, {sec:.1f} с аудио"))
        else:
            d = i2v_duration(sec)
            rows.append(dict(step=f"план {s['id']}", endpoint=cfg["i2v"], units=d,
                             what=f"картинка→видео {d} с под {sec:.1f} с аудио"))
    for r in rows:
        r["cost"] = cost_of(r["endpoint"], r["units"])
    return rows


def table(rows):
    out = ["| Шаг | Модель | Объём | Цена ед. | Сумма |", "|---|---|---|---|---|"]
    for r in rows:
        p = PRICES[r["endpoint"]]
        out.append(f"| {r['step']} | `{r['endpoint']}` | {r['what']} | {p['price']} $/{p['unit']} | {r['cost']:.3f} $ |")
    out.append(f"| **итого** | | | | **{sum(r['cost'] for r in rows):.2f} $** |")
    return "\n".join(out)
