#!/usr/bin/env python3
"""Сервер Lekar на Railway: хранит образцы и результаты на диске, запускает шаги run.py,
отдаёт видео по ссылке. Стандартная библиотека, всё под одним токеном.

    GET    /                                живость -> ok
    GET    /box/<токен>                     ядра, память, диск, что идёт
    GET    /f/<токен>/[путь/]               список файлов
    GET    /f/<токен>/<путь>                скачать (Range)
    PUT    /f/<токен>/<путь>[?part=N&done=1] положить; part=1 начинает заново, дальше дописывает
    DELETE /f/<токен>/<путь>                удалить
    POST   /run/<токен>/<образец>/<шаг>     запустить: тело json {mode, yes, pro, redo, voice, seed}
    GET    /run/<токен>/                    что идёт / чем кончилось
    GET    /run/<токен>/<образец>/log       хвост журнала

Пути файлов — от /data: samples/01/original.mp4, work/01/A/replica_01.mp4 ...
"""
import hmac, json, os, re, shutil, subprocess, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

APP = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("LEKAR_DATA", "/data")).resolve()
TOKEN = os.environ.get("LEKAR_SERVER_TOKEN", "")
STEPS = {"estimate", "voices", "all", "voice", "slice", "frames", "animate", "assemble", "compare", "report"}
NAME = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
RUNS, LOCK = {}, threading.Lock()


def sync_samples():
    """Образцы из репозитория -> диск; уже лежащие на диске файлы не перезаписываются."""
    for src in (APP / "samples").rglob("*"):
        dst = DATA / "samples" / src.relative_to(APP / "samples")
        if src.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
        elif not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(src, dst)


def safe(rel):
    p = (DATA / unquote(rel)).resolve()
    return p if p == DATA or str(p).startswith(str(DATA) + os.sep) else None


def box():
    mem = {}
    for line in open("/proc/meminfo"):
        k, v = line.split(":"); mem[k] = int(v.split()[0]) // 1024
    du = shutil.disk_usage(DATA)
    return dict(cores=os.cpu_count(), load=os.getloadavg(), mem_mb=dict(total=mem["MemTotal"], free=mem["MemAvailable"]),
                disk_gb=dict(total=round(du.total / 2**30, 1), free=round(du.free / 2**30, 1)), runs=status())


def status():
    with LOCK:
        out = {}
        for k, r in RUNS.items():
            code = r["proc"].poll()
            out[k] = dict(step=r["step"], args=r["args"], started=r["started"],
                          status="running" if code is None else ("done" if code == 0 else ("needs --yes" if code == 2 else "failed")),
                          code=code, finished=r.get("finished"))
        return out


def launch(sample, step, body):
    args = [sys.executable, str(APP / "run.py"), step, "--sample", sample]
    if body.get("mode") in ("A", "B"):
        args += ["--mode", body["mode"]]
    if body.get("yes"):
        args.append("--yes")
    if isinstance(body.get("pro"), int):
        args += ["--pro", str(body["pro"])]
    if body.get("redo"):
        args += ["--redo"] + [str(int(x)) for x in body["redo"]]
    if body.get("voice") and NAME.match(str(body["voice"])):
        args += ["--voice", body["voice"]]
    if isinstance(body.get("seed"), int):
        args += ["--seed", str(body["seed"])]
    log_dir = DATA / "work" / sample; log_dir.mkdir(parents=True, exist_ok=True)
    with LOCK:
        cur = RUNS.get(sample)
        if cur and cur["proc"].poll() is None:
            return None
        log = open(log_dir / "run.log", "a")
        log.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} {' '.join(args[2:])}\n"); log.flush()
        p = subprocess.Popen(args, cwd=APP, stdout=log, stderr=subprocess.STDOUT, env=dict(os.environ, LEKAR_DATA=str(DATA)))
        RUNS[sample] = dict(proc=p, step=step, args=args[2:], started=time.time())

    def reap():
        p.wait(); log.close()
        with LOCK:
            RUNS[sample]["finished"] = time.time()
    threading.Thread(target=reap, daemon=True).start()
    return RUNS[sample]


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code, body, ctype="application/json"):
        if not isinstance(body, bytes):
            body = (json.dumps(body, ensure_ascii=False, indent=1) if ctype == "application/json" else str(body)).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith(("text", "application/json")) else ""))
        self.send_header("Content-Length", str(len(body))); self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _route(self):
        u = urlsplit(self.path)
        parts = u.path.split("/", 3)          # '', kind, token, rest
        if len(parts) < 3 or not TOKEN or not hmac.compare_digest(parts[2], TOKEN):
            return None, None, None
        return parts[1], (parts[3] if len(parts) > 3 else ""), parse_qs(u.query)

    def log_message(self, fmt, *a):
        sys.stderr.write("%s %s\n" % (self.command, re.sub(r"/[^/]{20,}/", "/***/", self.path.split("?")[0])))

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if self.path == "/":
            return self._send(200, "ok", "text/plain")
        kind, rest, q = self._route()
        if kind == "box":
            return self._send(200, box())
        if kind == "run":
            m = re.match(r"^([A-Za-z0-9_-]+)/log$", rest)
            if m:
                p = DATA / "work" / m.group(1) / "run.log"
                n = int(q.get("n", ["6000"])[0])
                return self._send(200, p.read_bytes()[-n:] if p.exists() else b"", "text/plain")
            return self._send(200, status())
        if kind == "f":
            p = safe(rest)
            if p is None or not p.exists():
                return self._send(404, {"error": "нет файла"})
            if p.is_dir():
                return self._send(200, [dict(path=str(f.relative_to(DATA)), size=f.stat().st_size)
                                        for f in sorted(p.rglob("*")) if f.is_file()])
            return self._file(p)
        self._send(404, {"error": "не найдено"})

    def _file(self, p):
        size = p.stat().st_size
        ctype = {".mp4": "video/mp4", ".wav": "audio/wav", ".png": "image/png", ".jpg": "image/jpeg",
                 ".md": "text/markdown", ".json": "application/json", ".ass": "text/plain"}.get(p.suffix, "application/octet-stream")
        a, b, code = 0, size - 1, 200
        m = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                a = int(m.group(1)); b = int(m.group(2)) if m.group(2) else size - 1
            else:
                a = size - int(m.group(2))
            b = min(b, size - 1); code = 206
        self.send_response(code)
        self.send_header("Content-Type", ctype); self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(b - a + 1))
        if code == 206:
            self.send_header("Content-Range", f"bytes {a}-{b}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(p, "rb") as f:
            f.seek(a); left = b - a + 1
            while left > 0:
                chunk = f.read(min(1 << 20, left))
                if not chunk:
                    break
                self.wfile.write(chunk); left -= len(chunk)

    def _body(self):
        n = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(n) if n else b""

    def do_PUT(self):
        kind, rest, q = self._route()
        if kind != "f":
            return self._send(404, {"error": "не найдено"})
        p = safe(rest)
        if p is None or p == DATA:
            return self._send(400, {"error": "плохой путь"})
        part = int(q.get("part", ["0"])[0])
        tmp = p.with_name(p.name + ".part")
        p.parent.mkdir(parents=True, exist_ok=True)
        n = int(self.headers.get("Content-Length", 0))
        target = tmp if part else p.with_name(p.name + ".up")
        with open(target, "ab" if part > 1 else "wb") as f:
            while n > 0:
                chunk = self.rfile.read(min(1 << 20, n))
                if not chunk:
                    break
                f.write(chunk); n -= len(chunk)
        if not part or q.get("done"):
            os.replace(target, p)
        self._send(200, dict(path=str(p.relative_to(DATA)), size=(p if p.exists() else target).stat().st_size))

    def do_POST(self):
        kind, rest, q = self._route()
        m = re.match(r"^([A-Za-z0-9_-]{1,40})/([a-z]+)$", rest or "")
        if kind != "run" or not m or m.group(2) not in STEPS:
            return self._send(404, {"error": "не найдено"})
        try:
            body = json.loads(self._body() or b"{}")
        except ValueError:
            return self._send(400, {"error": "тело не json"})
        r = launch(m.group(1), m.group(2), body)
        if r is None:
            return self._send(409, {"error": "по этому образцу уже идёт шаг", "runs": status()})
        self._send(202, {"started": r["args"]})

    def do_DELETE(self):
        kind, rest, _ = self._route()
        p = safe(rest) if kind == "f" else None
        if p is None or p == DATA or not p.exists():
            return self._send(404, {"error": "нет файла"})
        shutil.rmtree(p) if p.is_dir() else p.unlink()
        self._send(200, {"deleted": rest})


def main():
    if not TOKEN:
        print("LEKAR_SERVER_TOKEN не задан — всё, кроме /, закрыто", flush=True)
    DATA.mkdir(parents=True, exist_ok=True)
    sync_samples()
    port = int(os.environ.get("PORT", 8080))
    print(f"lekar server :{port}, data {DATA}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever()


if __name__ == "__main__":
    main()
