# Lekar

Ролики с говорящим аватаром: текст → озвучка → нарезка по планам → кадры → оживление
(Kling на fal) → подгонка → субтитры → сборка → сравнение с образцом → отчёт.

## Где что считается — главное правило

- **Монтаж и рендер — только в контейнере Railway**, проект `lekar`,
  https://lekar-production.up.railway.app. Не в контейнере сессии: он временный.
- Генерация — на fal (`FAL_KEY` в окружении сессии и в переменных сервиса).
- Сессия подключается к серверу сама: хук `.claude/hooks/session-start.sh` берёт токен
  из Railway и пишет `~/.config/lekar/.env`. Проверка: `python3 tools/remote.py ping` → `ok`.
- Другие проекты на Railway (explainer-kit, Psy, Afon, Pravo, Video и др.) не трогать.

## Как работать

```bash
python3 tools/remote.py put <файл> samples/01/original.mp4
python3 tools/remote.py run 01 estimate --mode A && python3 tools/remote.py wait 01
python3 tools/remote.py run 01 all --mode A --yes && python3 tools/remote.py wait 01
python3 tools/remote.py url work/01/A/side_by_side.mp4
```

- Платные шаги (`--yes`) — только после показанного расчёта и «да» пользователя.
- Пушить в `master` = передеплой сервера; на время идущего шага не пушить.
- Новые образцы: `samples/<id>/{text.txt,shots.json,prompts.yaml}` в git, `original.mp4` — на сервер.
- Токен сервера и ключи в чат не выводить.

Подробности — `README.md`; конвейер — `run.py`, `lekar/`; сервер — `server/server.py`.
