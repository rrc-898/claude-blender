#!/usr/bin/env python3
"""Cliente del Claude Bridge: habla con la sesion viva de Blender.

Uso:
    cb.py ping
    cb.py scene [--raw]
    cb.py eval  "expresion python"
    cb.py exec  script.py          (o  cb.py exec -  para leer de stdin)
    cb.py shot  [window|area|viewport] [--out RUTA] [--max-dim N]
"""
import argparse
import json
import os
import socket
import sys

HOST = "127.0.0.1"
BASE_DIR = os.path.expanduser("~/claude-blender")
DEFAULT_PORT = 51707


def _port():
    try:
        with open(os.path.join(BASE_DIR, ".port")) as fh:
            return int(fh.read().strip())
    except Exception:
        return DEFAULT_PORT


def _token():
    path = os.path.join(BASE_DIR, ".token")
    try:
        with open(path) as fh:
            return fh.read().strip()
    except FileNotFoundError:
        sys.exit("No existe %s todavia: arranca el add-on en Blender primero." % path)


def send(payload, timeout=300):
    payload["token"] = _token()
    port = _port()
    try:
        with socket.create_connection((HOST, port), timeout=10) as s:
            s.settimeout(timeout)
            s.sendall((json.dumps(payload) + "\n").encode())
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
    except ConnectionRefusedError:
        sys.exit("Nadie escucha en %s:%s. Activa 'Claude Bridge' en Blender "
                 "(barra lateral N > pestana Claude)." % (HOST, port))
    except socket.timeout:
        sys.exit("Timeout hablando con Blender.")
    if not buf:
        sys.exit("Blender cerro la conexion sin responder.")
    return json.loads(buf.decode())


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("ping")

    p_scene = sub.add_parser("scene")
    p_scene.add_argument("--raw", action="store_true")

    p_eval = sub.add_parser("eval")
    p_eval.add_argument("code")

    p_exec = sub.add_parser("exec")
    p_exec.add_argument("script", help="ruta a un .py, o '-' para stdin")
    p_exec.add_argument("--label", default="Claude", help="nombre del paso de deshacer")
    p_exec.add_argument("--no-undo", action="store_true")

    p_shot = sub.add_parser("shot")
    p_shot.add_argument("mode", nargs="?", default="window",
                        choices=["window", "area", "viewport"])
    p_shot.add_argument("--out")
    p_shot.add_argument("--max-dim", type=int, default=1600)

    a = ap.parse_args()

    if a.cmd == "ping":
        r = send({"action": "ping"})
    elif a.cmd == "scene":
        r = send({"action": "scene"})
        if not a.raw and r.get("ok"):
            print(json.dumps(r, indent=2, ensure_ascii=False))
            return
    elif a.cmd == "eval":
        r = send({"action": "eval", "code": a.code})
    elif a.cmd == "exec":
        code = sys.stdin.read() if a.script == "-" else open(a.script).read()
        r = send({"action": "exec", "code": code, "label": a.label,
                  "undo": not a.no_undo})
        if r.get("stdout"):
            sys.stdout.write(r["stdout"])
        if r.get("stderr"):
            sys.stderr.write(r["stderr"])
        if r.get("ok"):
            print("[ok] %s" % a.label)
        else:
            sys.stderr.write(r.get("traceback", "") or json.dumps(r))
            sys.exit(1)
        return
    elif a.cmd == "shot":
        req = {"action": "screenshot", "mode": a.mode, "max_dim": a.max_dim}
        if a.out:
            req["path"] = os.path.abspath(os.path.expanduser(a.out))
        r = send(req)
        if r.get("ok"):
            print("%s (%.0f KB)" % (r["path"], r["bytes"] / 1024.0))
            return

    print(json.dumps(r, indent=2, ensure_ascii=False))
    if not r.get("ok", True):
        sys.exit(1)


if __name__ == "__main__":
    main()
