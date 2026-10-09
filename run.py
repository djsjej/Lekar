#!/usr/bin/env python3
"""Конвейер копии образца: озвучка -> нарезка -> кадры -> оживление -> сборка -> сравнение -> отчёт.

    python3 run.py estimate --sample 01 --mode A
    python3 run.py voices   --sample 01 --yes            # пробы голосов, ~0,06 $
    python3 run.py all      --sample 01 --mode A --yes
    python3 run.py animate  --sample 01 --mode A --yes --pro 4    # перегенерировать план 4 в Pro

Платные шаги без --yes только показывают расчёт. Готовые файлы не генерируются повторно;
чтобы переделать, удалить файл или передать --redo N.
"""
import argparse, json, sys, time
from pathlib import Path

import yaml

from lekar import audio, estimate, video
from lekar.core import DATA, Ledger, ROOT, duration, load_env

STEPS = ["voice", "slice", "frames", "animate", "assemble", "compare", "report"]
EXTRA = ["lineup", "assemble_g", "share"]


class Run:
    def __init__(self, a):
        load_env()
        self.a = a
        self.cfg = yaml.safe_load((ROOT / "lekar/config.yaml").read_text())
        if a.voice:
            self.cfg["voice"] = a.voice
        if a.avatar:
            self.cfg["avatar"] = a.avatar
        if a.i2v:
            self.cfg["i2v"] = a.i2v
        self.sdir = DATA / "samples" / a.sample
        self.shots = json.loads((self.sdir / "shots.json").read_text())
        # часть ролика: озвучка и нарезка — по всему тексту, генерация и сборка — только по этим планам
        self.part = [s for s in self.shots if not a.shots or s["id"] in a.shots]
        self.suffix = f"_p{self.part[0]['id']}-{self.part[-1]['id']}" if a.shots else ""
        self.text = (self.sdir / "text.txt").read_text().strip()
        self.prompts = yaml.safe_load((self.sdir / "prompts.yaml").read_text())
        self.original = self.sdir / "original.mp4"
        self.base = DATA / "work" / a.sample
        self.adir = self.base / "audio"
        self.out = self.base / a.mode
        for d in (self.adir, self.out):
            d.mkdir(parents=True, exist_ok=True)
        self.ledger = Ledger(self.base / "ledger.json")

    # ---- расчёт ----
    def slices(self):
        p = self.adir / "slices.json"
        return json.loads(p.read_text()) if p.exists() else None

    def estimate(self, show=True):
        sl = self.slices()
        durs = {s["id"]: s["dur"] for s in sl} if sl else None
        rows = estimate.plan(self.part, self.text, self.a.mode, self.cfg, durs)
        if (self.adir / "voice.wav").exists() or (self.adir / "voice_raw.wav").exists():
            rows = [r for r in rows if r["step"] != "озвучка"]
        if self.a.mode == "B" and (self.out / "frame_4.png").exists():
            rows = [r for r in rows if r["step"] != "кадры"]
        for r in rows:
            if self.a.pro and r["step"] == f"план {self.a.pro}":
                r["endpoint"] = self.cfg["avatar_pro"]
                r["cost"] = estimate.cost_of(r["endpoint"], r["units"])
        if show:
            print(f"\nРасчёт: образец {self.a.sample}, режим {self.a.mode}"
                  f"{' (по реальной нарезке)' if durs else ' (по длинам планов оригинала)'}\n")
            print(estimate.table(rows) + "\n")
        return rows

    def gate(self, what):
        if not self.a.yes:
            print(f"Шаг «{what}» платный. Повторить с --yes, чтобы подтвердить расчёт выше.")
            sys.exit(2)

    def timed(self, name, fn):
        t0 = time.time(); r = fn(); self.ledger.step(f"{self.a.mode}:{name}" if name not in ("voice", "slice") else name, time.time() - t0)
        return r

    # ---- шаги ----
    def voices(self):
        first = self.shots[0]["text"]
        rows = [dict(step=f"проба {v}", endpoint=self.cfg["tts"], units=len(first) / 1000, what=f"{len(first)} симв.") for v in self.cfg["voices"]]
        for r in rows:
            r["cost"] = estimate.cost_of(r["endpoint"], r["units"])
        print(estimate.table(rows)); self.gate("пробы голосов")
        d = self.adir / "voices"; d.mkdir(exist_ok=True)
        for v in self.cfg["voices"]:
            if not (d / v / "voice.wav").exists():
                audio.tts(self.ledger, self.cfg, first, d / v, voice=v, tag=f"проба голоса {v}")
            print(f"  {v}: {d / v / 'voice.wav'}")

    def voice(self):
        if (self.adir / "words.json").exists() or (self.adir / "words_raw.json").exists():
            return
        self.estimate(); self.gate("озвучка")
        wav = self.adir / "voice.wav"
        if not wav.exists():
            audio.tts(self.ledger, self.cfg, self.text, self.adir, tag=f"озвучка {self.cfg['voice']}")
        raw = json.loads((self.adir / "tts_raw.json").read_text() or "null")
        words = audio.normalize_timestamps(raw)
        src = "синтез"
        if not words:
            words, src = audio.whisper_words(wav), "faster-whisper"
        words = audio.attach_script(words, self.shots)
        (self.adir / "words.json").write_text(json.dumps(words, ensure_ascii=False, indent=0))
        (self.adir / "meta.json").write_text(json.dumps(dict(voice=self.cfg["voice"], timings=src), ensure_ascii=False))
        print(f"  голос {self.cfg['voice']}, {duration(wav):.2f} с, тайминги: {src}")

    def slice(self):
        raw_wav, raw_words = self.adir / "voice_raw.wav", self.adir / "words_raw.json"
        if not raw_wav.exists():                      # первая нарезка: сохранить исходник синтеза
            (self.adir / "voice.wav").rename(raw_wav)
            (self.adir / "words.json").rename(raw_words)
        words = json.loads(raw_words.read_text())
        cuts = audio.cut_points(raw_wav, words)
        sl = audio.slice_audio(raw_wav, cuts, self.adir)
        if self.cfg.get("match_cuts") and all("orig_end" in s for s in self.shots):
            words, sl, notes = audio.match_cuts(raw_wav, words, sl, self.shots, self.adir)
            (self.adir / "match.json").write_text(json.dumps(notes, indent=1))
            for n in notes:
                print(f"  план {n['id']}: речь {n['speech']:.2f} с → {n['result']:.2f} с "
                      f"(цель {n['target']:.2f}, темп ×{n['tempo']:.3f}, пауза {n['pad']:.2f})")
        else:
            import shutil; shutil.copy(raw_wav, self.adir / "voice.wav")
        (self.adir / "words.json").write_text(json.dumps(words, ensure_ascii=False, indent=0))
        (self.adir / "slices.json").write_text(json.dumps(sl, indent=1))
        for s in sl:
            print(f"  план {s['id']}: {s['start']:.2f}–{s['end']:.2f} ({s['dur']:.2f} с)")

    def frames(self):
        if self.a.mode == "A":
            if not self.original.exists():
                sys.exit(f"нет {self.original} — режим A берёт кадры из оригинала")
            video.frames_from_original(self.original, self.shots, self.out)
        else:
            self.estimate(); self.gate("кадры")
            video.frames_generated(self.ledger, self.prompts, self.shots, self.out, seed=self.a.seed)
        print("  кадры:", ", ".join(str(self.out / f"frame_{s['id']}.png") for s in self.shots))

    def animate(self):
        sl = {s["id"]: s for s in self.slices()}
        todo = [s for s in self.part if not (self.out / f"raw_{s['id']}.mp4").exists() or s["id"] in self.a.redo]
        if not todo:
            return
        self.estimate(); self.gate("оживление")
        for s in todo:
            i, frame, dst = s["id"], self.out / f"frame_{s['id']}.png", self.out / f"raw_{s['id']}.mp4"
            if s["kind"] == "talking":
                ep = self.cfg["avatar_pro"] if self.a.pro == i else self.cfg["avatar"]
                video.animate_talking(self.ledger, ep, frame, sl[i]["path"], self.prompts["animate"][i], dst,
                                      f"{self.a.mode}: план {i} аватар {ep.rsplit('/', 1)[-1]}")
            else:
                d = video.i2v_seconds(self.cfg["i2v"], sl[i]["dur"], self.cfg["max_stretch"])
                video.animate_action(self.ledger, self.cfg["i2v"], frame, self.prompts["animate"][i], d, dst,
                                     f"{self.a.mode}: план {i} i2v {d} с")

    def assemble(self):
        sl = {s["id"]: s for s in self.slices()}
        words = json.loads((self.adir / "words.json").read_text())
        voice = self.adir / "voice.wav"
        if self.suffix:                                   # часть ролика: свой кусок голоса и слов
            t0, t1 = sl[self.part[0]["id"]]["start"], sl[self.part[-1]["id"]]["end"]
            ids = {s["id"] for s in self.part}
            words = [dict(w, start=round(w["start"] - t0, 3), end=round(w["end"] - t0, 3)) for w in words if w["shot"] in ids]
            voice = self.out / f"voice{self.suffix}.wav"
            from lekar.core import sh
            sh("ffmpeg", "-y", "-v", "error", "-i", self.adir / "voice.wav", "-ss", f"{t0:.3f}", "-to", f"{t1:.3f}", voice)
        ass = video.make_ass(words, self.out / f"subs{self.suffix}.ass", self.cfg)
        fits, parts = {}, []
        for s in self.part:
            p = self.out / f"shot_{s['id']}.mp4"
            fits[s["id"]] = video.fit(self.out / f"raw_{s['id']}.mp4", sl[s["id"]]["dur"], p, 1080, 1920, self.cfg["max_stretch"], s.get("delogo"), self.cfg.get("enhance"))
            parts.append(p)
        (self.out / "fit.json").write_text(json.dumps(fits, ensure_ascii=False, indent=1))
        tag = self.copy_path().stem
        for w, h, name in ((720, 1280, f"{tag}.mp4"), (1080, 1920, f"{tag}_1080.mp4")):
            video.assemble(parts, voice, ass, self.out / name, w, h)
            print(f"  {self.out / name}: {duration(self.out / name):.2f} с, {video.measure_lufs(self.out / name):.1f} LUFS")

    def copy_path(self):
        return self.out / (f"replica_{self.a.sample}" + ("" if self.a.mode == "A" else "_B") + self.suffix + ".mp4")

    def compare(self):
        if not self.original.exists():
            print("  нет оригинала — сравнение пропущено"); return
        dst = self.out / f"side_by_side{self.suffix}.mp4"
        orig = self.original
        if self.suffix:
            orig = self.out / f"original{self.suffix}.mp4"
            from lekar.core import sh
            sh("ffmpeg", "-y", "-v", "error", "-ss", str(self.part[0]["orig_start"]), "-to", str(self.part[-1]["orig_end"]),
               "-i", self.original, "-c:v", "libx264", "-crf", "16", "-c:a", "aac", orig)
        video.side_by_side(orig, self.copy_path(), dst)
        print(f"  {dst}")

    def lineup(self):
        """Тест моделей с голосом: work/<образец>/talk/clips.json -> клипы подряд с подписями."""
        from lekar import talk
        d = self.base / "talk"
        clips = json.loads((d / "clips.json").read_text())
        dst = d / "lineup.mp4"
        talk.lineup([(c["name"], d / c["file"]) for c in clips], dst)
        print(f"  {dst}: {duration(dst):.1f} с")

    def assemble_g(self):
        """Сборка ролика из клипов Grok со своим звуком: work/<образец>/G/plan.json от tools/grok_build.py."""
        from lekar.core import sh
        g = self.base / "G"
        plan = json.loads((g / "plan.json").read_text())
        parts, auds, words, t, cuts = [], [], [], 0.0, []
        for k, p in enumerate(plan):
            nxt = plan[k + 1] if k + 1 < len(plan) else None
            # склейка как в оригинале, если речь в неё влезает; иначе — сразу после речи.
            # Часть плана, за которой идёт продолжение, — до последнего кадра: следующая часть
            # стартует именно с него, иначе на стыке скачок позы.
            if nxt is not None and nxt.get("shot") == p.get("shot") and "." in str(nxt["id"]):
                length = p["clip"]
            elif p.get("orig") and p["speech_end"] + 0.25 <= p["orig"]:
                length = p["orig"]
            else:                                   # часть длинного плана или речь не влезла — режем после речи
                length = min(p["clip"], p["speech_end"] + 0.25)
            v = g / f"shot_{p['id']}.mp4"
            video.fit(g / p["file"], length, v, 1080, 1920, self.cfg["max_stretch"], p.get("delogo"), self.cfg.get("enhance"))
            prev = plan[k - 1] if k else None
            if prev is not None and prev.get("shot") == p.get("shot") and "." in str(p["id"]):
                # Grok чуть перерисовывает стартовый кадр (цвет, масштаб): на стыке частей — растворение
                # последнего кадра предыдущей части за 0,25 с поверх начала этой. Длина и звук те же.
                last = g / f"last_{prev['id']}.png"
                sh("ffmpeg", "-y", "-v", "error", "-sseof", "-0.05", "-i", parts[-1], "-frames:v", "1", "-update", "1", last)
                tmp = v.with_name(v.stem + "_x.mp4")
                sh("ffmpeg", "-y", "-v", "error", "-i", v, "-loop", "1", "-t", "0.5", "-i", last, "-filter_complex",
                   "[1]format=yuva420p,fade=t=out:st=0:d=0.25:alpha=1[o];[0][o]overlay=eof_action=pass,format=yuv420p",
                   "-c:v", "libx264", "-preset", "fast", "-crf", "8", "-an", tmp)
                tmp.replace(v)
            a = g / f"aud_{p['id']}.wav"
            sh("ffmpeg", "-y", "-v", "error", "-i", g / p["file"], "-vn", "-t", f"{length:.3f}", "-af", "apad",
               "-t", f"{length:.3f}", "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", a)
            words += [dict(w, start=round(w["start"] + t, 3), end=round(min(w["end"], length) + t, 3)) for w in p["words"]]
            parts.append(v); auds.append(a); t += length; cuts.append(round(t, 3))
        lst = g / "aud.txt"
        lst.write_text("".join(f"file '{x.resolve()}'\n" for x in auds))
        voice = g / "voice.wav"
        sh("ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst, "-c:a", "pcm_s16le", voice)
        ass = video.make_ass(words, g / "subs.ass", self.cfg)
        (g / "words.json").write_text(json.dumps(words, ensure_ascii=False))
        first, last = plan[0].get("shot", plan[0]["id"]), plan[-1].get("shot", plan[-1]["id"])
        ids = f"_p{first}-{last}"
        for w, h, name in ((720, 1280, f"replica_{self.a.sample}_grok{ids}.mp4"), (1080, 1920, f"replica_{self.a.sample}_grok{ids}_1080.mp4")):
            video.assemble(parts, voice, ass, g / name, w, h)
            print(f"  {g / name}: {duration(g / name):.2f} с, {video.measure_lufs(g / name):.1f} LUFS")
        shots = {s["id"]: s for s in self.shots}
        print("  склейки:", ", ".join(f"{c:.2f}" + (f" (ориг. {shots[p['shot']]['orig_end']:.2f})" if p.get("orig") or p is plan[-1] else "")
                                       for c, p in zip(cuts, plan)))
        if self.original.exists():
            orig = g / f"original{ids}.mp4"
            sh("ffmpeg", "-y", "-v", "error", "-ss", str(shots[first]["orig_start"]), "-to", str(shots[last]["orig_end"]),
               "-i", self.original, "-c:v", "libx264", "-crf", "16", "-c:a", "aac", orig)
            video.side_by_side(orig, g / f"replica_{self.a.sample}_grok{ids}.mp4", g / f"side_by_side_grok{ids}.mp4")
            print(f"  {g / f'side_by_side_grok{ids}.mp4'}")

    def share(self):
        """Копия 1080×1920 для чата: не больше 29 МБ (лимит 30 МБ), двухпроходное сжатие под размер."""
        from lekar.core import sh
        g = self.base / "G"
        for src in sorted(g.glob("replica_*_1080.mp4")):
            dst = src.with_name(src.stem.replace("_1080", "_1080_chat") + ".mp4")
            dur = duration(src)
            kbps = int((29 * 8 * 1024 * 0.97) / dur - 160)          # видео; 160 кбит/с — звук
            log = g / "x264pass"
            for pas, out, extra in ((1, "/dev/null", ["-an", "-f", "mp4"]), (2, dst, ["-c:a", "aac", "-b:a", "160k"])):
                sh("ffmpeg", "-y", "-v", "error", "-i", src, "-c:v", "libx264", "-preset", "slow", "-b:v", f"{kbps}k",
                   "-pass", str(pas), "-passlogfile", log, "-pix_fmt", "yuv420p", *extra, "-movflags", "+faststart", out)
            print(f"  {dst}: {dst.stat().st_size / 2**20:.1f} МБ, {kbps} кбит/с")

    def report(self):
        sl = self.slices()
        fits = json.loads((self.out / "fit.json").read_text()) if (self.out / "fit.json").exists() else {}
        meta = json.loads((self.adir / "meta.json").read_text())
        calls = self.ledger.data["calls"]
        mine = [c for c in calls if c["tag"].startswith(f"{self.a.mode}:") or c["tag"].startswith("озвучка")]
        est = estimate.plan(self.part, self.text, self.a.mode, self.cfg)
        L = [f"# Отчёт: копия образца {self.a.sample}, режим {self.a.mode}"
             + (f", планы {self.part[0]['id']}–{self.part[-1]['id']}" if self.suffix else ""), "",
             f"Модели: говорящие планы `{self.cfg['avatar']}`, действия `{self.cfg['i2v']}`.", "",
             f"Голос: {meta['voice']} ({self.cfg['tts']}), тайминги слов: {meta['timings']}.", "",
             "## Деньги", "", "Расчёт до генерации (по длинам планов оригинала):", "", estimate.table(est), "",
             "Факт:", "", "| Вызов | Модель | Объём | Сумма | Время |", "|---|---|---|---|---|"]
        for c in mine:
            L.append(f"| {c['tag']} | `{c['endpoint']}` | {c['units']} | {c['cost']:.3f} $ | {c['seconds']:.0f} с |"
                     + (" ошибка" if c.get("error") else ""))
        L += [f"| **итого** | | | **{sum(c['cost'] for c in mine):.2f} $** | |", ""]
        extra = [c for c in calls if c["tag"].startswith("проба")]
        if extra:
            L += [f"Пробы голосов отдельно: {sum(c['cost'] for c in extra):.3f} $.", ""]
        L += ["## Время по шагам", "", "| Шаг | Секунды |", "|---|---|"]
        for k, v in self.ledger.data["steps"].items():
            if ":" not in k or k.startswith(f"{self.a.mode}:"):
                L.append(f"| {k} | {v} |")
        L += ["", "## Склейки", "", "| Граница | Оригинал | Копия | Отклонение |", "|---|---|---|---|"]
        bad = []
        for s, cut in zip(self.shots, sl):
            if s is self.part[-1]:
                break
            d = cut["end"] - s["orig_end"]
            L.append(f"| {s['id']}→{s['id'] + 1} | {s['orig_end']:.2f} | {cut['end']:.2f} | {d:+.2f} с {'✅' if abs(d) <= 0.3 else '❌'} |")
            if abs(d) > 0.3:
                bad.append(f"склейка {s['id']}→{s['id'] + 1} уехала на {d:+.2f} с — темп голоса другой")
        last = self.part[-1]
        total = next(c["end"] for c in sl if c["id"] == last["id"])
        L += [f"| конец | {last['orig_end']:.2f} | {total:.2f} | {total - last['orig_end']:+.2f} с |", ""]
        L += ["## Подгонка планов", "", "| План | Было | Нужно | Что сделано |", "|---|---|---|---|"]
        for k, f in fits.items():
            L.append(f"| {k} | {f['have']:.2f} с | {f['need']:.2f} с | {f['note']} |")
        cp = self.copy_path()
        if cp.exists():
            L += ["", f"Громкость копии: {video.measure_lufs(cp):.1f} LUFS (цель −14, оригинал −14,5)."]
        L += ["", "## Что не совпало", ""] + [f"- {b}" for b in bad] + ["- (дописать по просмотру side_by_side.mp4: губы планов 1 и 4, руки планов 2–3, субтитры)", ""]
        (self.out / f"report{self.suffix}.md").write_text("\n".join(L))
        print(f"  {self.out / f'report{self.suffix}.md'}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["estimate", "voices", "all"] + STEPS + EXTRA)
    ap.add_argument("--sample", default="01")
    ap.add_argument("--mode", choices=["A", "B"], default="A")
    ap.add_argument("--yes", action="store_true", help="подтвердить расчёт и тратить деньги")
    ap.add_argument("--voice")
    ap.add_argument("--pro", type=int, help="план, который оживить в Kling Avatar Pro")
    ap.add_argument("--redo", type=int, nargs="*", default=[], help="планы, которые перегенерировать")
    ap.add_argument("--seed", type=int, default=55)
    ap.add_argument("--shots", type=int, nargs="*", help="только эти планы (например 1 2 3 — первые 20 с)")
    ap.add_argument("--avatar", help="модель говорящих планов, например veed/fabric-1.0")
    ap.add_argument("--i2v", help="модель планов с действием, например xai/grok-imagine-video/image-to-video")
    a = ap.parse_args()
    r = Run(a)
    if a.step == "estimate":
        r.estimate(); return
    if a.step == "voices":
        r.voices(); return
    steps = STEPS if a.step == "all" else [a.step]
    if a.step == "all":
        r.estimate(); r.gate("весь прогон")
    for s in steps:
        print(f"== {s}")
        r.timed(s, getattr(r, s))


if __name__ == "__main__":
    main()
