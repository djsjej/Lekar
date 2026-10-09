#!/usr/bin/env python3
"""Клиент сервера Lekar на Railway. Передача через curl — он уже знает прокси сессии.

    python3 tools/remote.py ping | box | status | ls [префикс] | url <путь>
    python3 tools/remote.py put <локальный> <путь>      кусками по 50 МБ
    python3 tools/remote.py get <путь> <локальный>
    python3 tools/remote.py rm <путь>
    python3 tools/remote.py run <образец> <шаг> [--mode A] [--yes] [--pro 4] [--redo 1 4] [--voice X]
    python3 tools/remote.py log <образец> [-n 6000]
    python3 tools/remote.py wait <образец>              ждать конца шага, потом хвост журнала

Настройки: LEKAR_SERVER_URL и LEKAR_SERVER_TOKEN в окружении или в ~/.config/lekar/.env.
"""
import argparse, json, os, subprocess, sys, time
from urllib.parse import quote

CHUNK = 50 * 2**20


def cfg():
    env = dict(os.environ)
    p = os.path.expanduser("~/.config/lekar/.env")
    if os.path.exists(p):
        for line in open(p):
            if "=" in line and not line.startswith("#"):
                k, v = line.strip().split("=", 1); env.setdefault(k, v)
    url, tok = env.get("LEKAR_SERVER_URL", "").rstrip("/"), env.get("LEKAR_SERVER_TOKEN", "")
    if not url or not tok:
        sys.exit("нужны LEKAR_SERVER_URL и LEKAR_SERVER_TOKEN (окружение или ~/.config/lekar/.env)")
    return url, tok


def curl(*args):
    r = subprocess.run(["curl", "-sS", "--fail-with-body", "--max-time", "1800", *args], capture_output=True)
    if r.returncode:
        sys.exit(f"curl {r.returncode}: {r.stderr.decode().strip()} {r.stdout.decode(errors='replace')[:500]}")
    return r.stdout


def js(raw):
    try:
        return json.loads(raw)
    except ValueError:
        return raw.decode(errors="replace")


def main():
    url, tok = cfg()
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd")
    ap.add_argument("rest", nargs="*")
    ap.add_argument("--mode"); ap.add_argument("--yes", action="store_true")
    ap.add_argument("--pro", type=int); ap.add_argument("--redo", type=int, nargs="*")
    ap.add_argument("--voice"); ap.add_argument("--seed", type=int)
    ap.add_argument("--shots", type=int, nargs="*"); ap.add_argument("--avatar"); ap.add_argument("--i2v")
    ap.add_argument("-n", type=int, default=6000)
    a = ap.parse_args()
    f = lambda path="": f"{url}/f/{tok}/{quote(path)}"
    c, r = a.cmd, a.rest
    if c == "ping":
        print(curl(f"{url}/").decode())
    elif c == "box":
        print(json.dumps(js(curl(f"{url}/box/{tok}")), ensure_ascii=False, indent=1))
    elif c == "status":
        print(json.dumps(js(curl(f"{url}/run/{tok}/")), ensure_ascii=False, indent=1))
    elif c == "ls":
        pre = r[0].strip("/") + "/" if r else ""
        for x in js(curl(f(pre))):
            print(f"{x['size']:>12}  {x['path']}")
    elif c == "url":
        print(f(r[0]))
    elif c == "put":
        src, dst = r
        size = os.path.getsize(src)
        if size <= CHUNK:
            print(js(curl("-T", src, f(dst))))
        else:
            n = (size + CHUNK - 1) // CHUNK
            for i in range(n):
                part = subprocess.run(["dd", f"if={src}", f"bs={CHUNK}", f"skip={i}", "count=1", "status=none"],
                                      capture_output=True).stdout
                q = f"?part={i + 1}" + ("&done=1" if i == n - 1 else "")
                print(i + 1, "/", n, js(subprocess.run(["curl", "-sS", "--max-time", "1800", "-X", "PUT", "--data-binary", "@-",
                                                       f(dst) + q], input=part, capture_output=True).stdout))
    elif c == "get":
        src, dst = r
        curl("-C", "-", "-o", dst, f(src)); print(dst)
    elif c == "rm":
        print(js(curl("-X", "DELETE", f(r[0]))))
    elif c == "run":
        sample, step = r
        body = {k: v for k, v in dict(mode=a.mode, yes=a.yes, pro=a.pro, redo=a.redo, voice=a.voice, seed=a.seed,
                                                     shots=a.shots, avatar=a.avatar, i2v=a.i2v).items() if v}
        print(js(curl("-X", "POST", "-H", "Content-Type: application/json", "--data-binary", json.dumps(body),
                      f"{url}/run/{tok}/{sample}/{step}")))
    elif c == "log":
        print(curl(f"{url}/run/{tok}/{r[0]}/log?n={a.n}").decode(errors="replace"))
    elif c == "wait":
        while True:
            st = js(curl(f"{url}/run/{tok}/")).get(r[0], {})
            if st.get("status") != "running":
                break
            time.sleep(10)
        print(curl(f"{url}/run/{tok}/{r[0]}/log?n={a.n}").decode(errors="replace"))
        print("статус:", st.get("status"))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
