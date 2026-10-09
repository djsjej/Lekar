"""Общее: пути, .env, вызовы fal с учётом денег и времени, ffmpeg."""
import json, mimetypes, os, subprocess, time, urllib.request, urllib.error
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
FONTS = ROOT / "fonts"
PRICES = yaml.safe_load((Path(__file__).parent / "prices.yaml").read_text())


def load_env():
    p = ROOT / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def fal_key():
    load_env()
    k = os.environ.get("FAL_KEY")
    if not k:
        raise SystemExit("FAL_KEY не задан (окружение или .env)")
    return k


def _req(method, url, body=None, headers=None, timeout=120):
    h = {"Authorization": f"Key {fal_key()}"} if "fal" in url and "fal.media" not in url else {}
    h.update(headers or {})
    data = None
    if body is not None and not isinstance(body, bytes):
        data = json.dumps(body).encode(); h["Content-Type"] = "application/json"
    elif isinstance(body, bytes):
        data = body
    r = urllib.request.Request(url, data=data, method=method, headers=h)
    for attempt in range(4):
        try:
            with urllib.request.urlopen(r, timeout=timeout) as resp:
                raw = resp.read()
                return json.loads(raw) if raw[:1] in (b"{", b"[") else raw
        except urllib.error.HTTPError as e:
            msg = e.read().decode(errors="replace")[:2000]
            if e.code >= 500 and attempt < 3:
                time.sleep(2 ** attempt); continue
            raise RuntimeError(f"{method} {url} -> {e.code}: {msg}")
        except (urllib.error.URLError, TimeoutError):
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)


def upload(path):
    """Файл -> публичная ссылка в хранилище fal. Кэш по размеру и mtime."""
    path = Path(path)
    cache = path.with_name(path.name + ".url")
    sig = f"{path.stat().st_size}:{int(path.stat().st_mtime)}"
    if cache.exists():
        s, url = cache.read_text().split("\n", 1)
        if s == sig:
            return url.strip()
    ct = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    r = _req("POST", "https://rest.alpha.fal.ai/storage/upload/initiate?storage_type=fal-cdn-v3",
             {"content_type": ct, "file_name": path.name})
    _req("PUT", r["upload_url"], path.read_bytes(), {"Content-Type": ct}, timeout=600)
    cache.write_text(f"{sig}\n{r['file_url']}")
    return r["file_url"]


def download(url, dst):
    dst = Path(dst); dst.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=600) as r, open(dst, "wb") as f:
        f.write(r.read())
    return dst


def cost_of(endpoint, units):
    p = PRICES[endpoint]
    return round(units * p["price"], 4)


class Ledger:
    """Журнал прогона: каждый платный вызов и каждый шаг со временем."""

    def __init__(self, path):
        self.path = Path(path)
        self.data = json.loads(self.path.read_text()) if self.path.exists() else {"calls": [], "steps": {}}

    def save(self):
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=1))

    def step(self, name, seconds):
        self.data["steps"][name] = round(self.data["steps"].get(name, 0) + seconds, 1)
        self.save()

    def call(self, **kw):
        self.data["calls"].append(kw); self.save()


def fal_run(ledger, endpoint, args, units, tag, poll=3):
    """Очередь fal: отправить, дождаться, вернуть результат. Пишет цену и время в журнал."""
    t0 = time.time()
    sub = _req("POST", f"https://queue.fal.run/{endpoint}", args)
    rid = sub["request_id"]
    print(f"  fal {endpoint} [{tag}] request {rid}", flush=True)
    while True:
        st = _req("GET", sub["status_url"])
        if st.get("status") == "COMPLETED":
            break
        if st.get("status") not in ("IN_QUEUE", "IN_PROGRESS"):
            raise RuntimeError(f"{endpoint}: {st}")
        time.sleep(poll)
    try:
        out = _req("GET", sub["response_url"], timeout=300)
    except RuntimeError as e:
        ledger.call(tag=tag, endpoint=endpoint, request_id=rid, units=units,
                    cost=cost_of(endpoint, units), seconds=round(time.time() - t0, 1), error=str(e)[:500])
        raise
    ledger.call(tag=tag, endpoint=endpoint, request_id=rid, units=units,
                cost=cost_of(endpoint, units), seconds=round(time.time() - t0, 1))
    return out


def sh(*cmd, quiet=True):
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"{' '.join(map(str, cmd))[:300]}\n{r.stderr[-3000:]}")
    return r.stdout + r.stderr if not quiet else r.stdout


def duration(path):
    return float(sh("ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path).strip())


def ensure_fonts():
    FONTS.mkdir(exist_ok=True)
    f = FONTS / "Montserrat-SemiBold.ttf"
    if not f.exists():
        download("https://raw.githubusercontent.com/JulietaUla/Montserrat/master/fonts/ttf/Montserrat-SemiBold.ttf", f)
    return f
