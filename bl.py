#!/usr/bin/env python3
"""Cliente de Claude Bridge.  bl.py ACTION [codigo|-f fichero] [--json k=v ...]"""
import json, os, socket, sys
BASE = os.path.expanduser("~/claude-blender")
tok = open(os.path.join(BASE, ".token")).read().strip()
port = int(open(os.path.join(BASE, ".port")).read().strip()) if os.path.exists(os.path.join(BASE, ".port")) else 51707
action = sys.argv[1]; req = {"action": action, "token": tok}
rest = sys.argv[2:]
if rest[:1] == ["-f"]:
    req["code"] = open(rest[1]).read(); rest = rest[2:]
elif rest and action in ("exec", "eval"):
    req["code"] = rest[0]; rest = rest[1:]
for kv in rest:
    k, v = kv.split("=", 1)
    try: v = json.loads(v)
    except Exception: pass
    req[k] = v
s = socket.create_connection(("127.0.0.1", port), timeout=300)
s.sendall((json.dumps(req) + "\n").encode()); buf = b""
while not buf.endswith(b"\n"):
    c = s.recv(1 << 20)
    if not c: break
    buf += c
r = json.loads(buf)
if action == "exec":
    sys.stdout.write(r.get("stdout", "")); sys.stderr.write(r.get("stderr", ""))
    if not r.get("ok"): print(r.get("traceback") or r); sys.exit(1)
else:
    print(json.dumps(r, indent=1, ensure_ascii=False)[:20000])
