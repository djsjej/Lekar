# Lekar — ролики с говорящим аватаром

Конвейер: текст → озвучка → нарезка по планам → стартовые кадры → оживление (Kling на fal)
→ подгонка → субтитры → сборка 720×1280 и 1080×1920 → сравнение с образцом → отчёт.

Генерация идёт на fal (ключ `FAL_KEY` в окружении или в `.env`), монтаж — локально через ffmpeg.

## Запуск

```bash
python3 run.py estimate --sample 01 --mode A        # расчёт, бесплатно
python3 run.py voices   --sample 01 --yes           # пробы голосов на первой фразе
python3 run.py all      --sample 01 --mode A --yes  # весь прогон
python3 run.py animate  --sample 01 --mode A --yes --pro 4 --redo 4   # план 4 заново в Pro
python3 run.py assemble --sample 01 --mode A && python3 run.py report --sample 01 --mode A
```

Без `--yes` платные шаги показывают расчёт и останавливаются. Готовые файлы повторно
не генерируются; журнал денег и времени — `work/<образец>/ledger.json`.

## Образец

`samples/<id>/`: `original.mp4` (не в git), `text.txt`, `shots.json`, `prompts.yaml`.

## Результат

`work/<id>/audio/` — `voice_raw.wav` (синтез), `voice.wav` (подогнанный под склейки),
`words.json`, `slice_N.wav`. `work/<id>/<режим>/` — кадры, `raw_N.mp4`, `shot_N.mp4`,
`subs.ass`, `replica_<id>.mp4`, `replica_<id>_1080.mp4`, `side_by_side.mp4`, `report.md`.

## Настройки

`lekar/config.yaml` — модели, голос, стиль субтитров, `match_cuts` (подгонка пауз и темпа
под склейки оригинала: пауза 0,15–0,8 с, темп не больше ±10%).
`lekar/prices.yaml` — цены fal, проверены 2026-10-09.

## Сервер на Railway

Монтаж и сборка идут в контейнере Railway (проект `lekar`), генерация — на fal.
`server/server.py` хранит образцы и результаты на диске `/data`, запускает шаги `run.py`
и отдаёт файлы по ссылке. Клиент — `tools/remote.py`, настройки в `~/.config/lekar/.env`:

    LEKAR_SERVER_URL=https://<домен>
    LEKAR_SERVER_TOKEN=<секрет>

Рабочий цикл:

    python3 tools/remote.py put original.mp4 samples/01/original.mp4
    python3 tools/remote.py run 01 estimate --mode A && python3 tools/remote.py wait 01
    python3 tools/remote.py run 01 all --mode A --yes && python3 tools/remote.py wait 01
    python3 tools/remote.py url work/01/A/side_by_side.mp4      # ссылка для браузера

Переменные сервиса: `FAL_KEY`, `LEKAR_SERVER_TOKEN`, `LEKAR_DATA=/data`.
Токен, попавший в переписку текстом, считается раскрытым — перевыпустить в Variables.
