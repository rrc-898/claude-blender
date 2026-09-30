bl_info = {
    "name": "Claude Bridge",
    "author": "Claude",
    "version": (1, 0, 0),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar (N) > Claude",
    "description": "Servidor local que permite a Claude inspeccionar y modificar la sesion viva",
    "category": "Development",
}

import bpy
import os
import io
import json
import queue
import socket
import threading
import traceback
import contextlib

# --------------------------------------------------------------------------
# Configuracion
# --------------------------------------------------------------------------

HOST = "127.0.0.1"
PORT = 51707
BASE_DIR = os.path.expanduser("~/claude-blender")
TOKEN_PATH = os.path.join(BASE_DIR, ".token")
SHOT_DIR = os.path.join(BASE_DIR, "shots")

# Cola de peticiones que el hilo del socket llena y el timer del hilo
# principal vacia. La API de Blender NO es thread-safe: todo bpy.* tiene que
# ejecutarse en el hilo principal o Blender revienta.
_requests = queue.Queue()
_server = None
_state = {"running": False, "port": None, "last": "-"}


def _token():
    """Lee el token compartido; lo crea si no existe."""
    os.makedirs(BASE_DIR, exist_ok=True)
    if not os.path.exists(TOKEN_PATH):
        tok = os.urandom(24).hex()
        fd = os.open(TOKEN_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(tok)
        return tok
    with open(TOKEN_PATH) as fh:
        return fh.read().strip()


# --------------------------------------------------------------------------
# Utilidades de contexto (para operadores que exigen ventana/area/region)
# --------------------------------------------------------------------------

def _find_view3d():
    """Devuelve (window, area, region) del primer View3D abierto, o Nones."""
    wm = bpy.context.window_manager
    for win in wm.windows:
        for area in win.screen.areas:
            if area.type != 'VIEW_3D':
                continue
            for region in area.regions:
                if region.type == 'WINDOW':
                    return win, area, region
    return None, None, None


def _first_window():
    wm = bpy.context.window_manager
    return wm.windows[0] if wm.windows else None


def _redraw_all():
    """Fuerza el refresco de la UI para que el usuario vea los cambios."""
    for win in bpy.context.window_manager.windows:
        for area in win.screen.areas:
            area.tag_redraw()


def _downscale(path, max_dim):
    """Reescala la imagen guardada para que el lado mayor sea <= max_dim."""
    if not max_dim:
        return path
    img = bpy.data.images.load(path, check_existing=False)
    try:
        w, h = img.size
        if max(w, h) > max_dim:
            scale = max_dim / float(max(w, h))
            img.scale(max(1, int(w * scale)), max(1, int(h * scale)))
            img.file_format = 'PNG'
            img.save(filepath=path)
    finally:
        bpy.data.images.remove(img)
    return path


# --------------------------------------------------------------------------
# Acciones
# --------------------------------------------------------------------------

def _act_ping(req):
    scn = bpy.context.scene
    return {
        "blender": bpy.app.version_string,
        "file": bpy.data.filepath or "(sin guardar)",
        "scene": scn.name,
        "objects": len(bpy.data.objects),
        "engine": scn.render.engine,
        "frame": scn.frame_current,
    }


def _act_exec(req):
    """Ejecuta codigo Python arbitrario en el hilo principal de Blender."""
    code = req.get("code", "")
    out, err = io.StringIO(), io.StringIO()
    ns = {"bpy": bpy, "__name__": "__claude__"}
    result = {}
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            exec(compile(code, "<claude>", "exec"), ns)
            result["ok"] = True
        except Exception:
            result["ok"] = False
            result["traceback"] = traceback.format_exc()
    result["stdout"] = out.getvalue()
    result["stderr"] = err.getvalue()
    # Punto de deshacer: el usuario puede revertir con Ctrl+Z lo que yo haga.
    if result["ok"] and req.get("undo", True):
        try:
            bpy.ops.ed.undo_push(message=req.get("label", "Claude"))
        except Exception:
            pass
    _redraw_all()
    return result


def _act_eval(req):
    ns = {"bpy": bpy}
    try:
        return {"ok": True, "value": repr(eval(req.get("code", ""), ns))}
    except Exception:
        return {"ok": False, "traceback": traceback.format_exc()}


def _act_screenshot(req):
    """mode: window (UI completa) | area (solo el View3D) | viewport (render OpenGL limpio)."""
    mode = req.get("mode", "window")
    os.makedirs(SHOT_DIR, exist_ok=True)
    path = req.get("path") or os.path.join(SHOT_DIR, "%s.png" % mode)
    path = os.path.abspath(os.path.expanduser(path))
    if os.path.exists(path):
        os.remove(path)

    win, area, region = _find_view3d()

    if mode == "viewport":
        if not (win and area and region):
            return {"ok": False, "error": "no hay ningun area View3D abierta"}
        with bpy.context.temp_override(window=win, screen=win.screen,
                                       area=area, region=region):
            bpy.ops.render.opengl('EXEC_DEFAULT', view_context=True,
                                  write_still=False)
        img = bpy.data.images.get("Render Result")
        if img is None:
            return {"ok": False, "error": "no se genero Render Result"}
        img.save_render(filepath=path)
    else:
        target = win or _first_window()
        if target is None:
            return {"ok": False, "error": "no hay ventana (modo background?)"}
        override = {"window": target, "screen": target.screen}
        op = bpy.ops.screen.screenshot
        if mode == "area":
            if area is None:
                return {"ok": False, "error": "no hay ningun area View3D abierta"}
            override["area"] = area
            op = bpy.ops.screen.screenshot_area
        with bpy.context.temp_override(**override):
            op('EXEC_DEFAULT', filepath=path, check_existing=False)

    if not os.path.exists(path):
        return {"ok": False, "error": "el operador no escribio %s" % path}
    _downscale(path, req.get("max_dim", 1600))
    return {"ok": True, "path": path, "bytes": os.path.getsize(path)}


def _act_scene(req):
    """Volcado estructurado de la escena: mas barato y preciso que una captura."""
    scn = bpy.context.scene
    r = scn.render

    def xform(o):
        return {
            "loc": [round(v, 4) for v in o.location],
            "rot_euler": [round(v, 4) for v in o.rotation_euler],
            "scale": [round(v, 4) for v in o.scale],
        }

    objects = []
    for o in bpy.data.objects:
        d = {"name": o.name, "type": o.type, "visible": not o.hide_render,
             "materials": [s.material.name if s.material else None for s in o.material_slots],
             **xform(o)}
        if o.type == 'MESH':
            d["verts"] = len(o.data.vertices)
            d["modifiers"] = [(m.name, m.type) for m in o.modifiers]
        elif o.type == 'LIGHT':
            d["light"] = {"type": o.data.type, "energy": o.data.energy,
                          "color": [round(c, 3) for c in o.data.color]}
            if o.data.type in {'POINT', 'SPOT'}:
                d["light"]["radius"] = o.data.shadow_soft_size
            elif o.data.type == 'AREA':
                d["light"]["size"] = o.data.size
        elif o.type == 'CAMERA':
            d["camera"] = {"lens": o.data.lens, "type": o.data.type,
                           "dof": o.data.dof.use_dof}
        objects.append(d)

    return {
        "ok": True,
        "file": bpy.data.filepath or "(sin guardar)",
        "scene": scn.name,
        "frame_range": [scn.frame_start, scn.frame_end, scn.frame_current],
        "render": {
            "engine": r.engine,
            "resolution": [r.resolution_x, r.resolution_y, r.resolution_percentage],
            "film_transparent": r.film_transparent,
            "samples": getattr(scn.cycles, "samples", None) if hasattr(scn, "cycles") else None,
            "view_transform": scn.view_settings.view_transform,
            "look": scn.view_settings.look,
        },
        "camera": scn.camera.name if scn.camera else None,
        "world": scn.world.name if scn.world else None,
        "objects": objects,
        "materials": [m.name for m in bpy.data.materials],
        "node_groups": [(g.name, g.bl_idname) for g in bpy.data.node_groups],
        "collections": [c.name for c in bpy.data.collections],
    }


ACTIONS = {
    "ping": _act_ping,
    "exec": _act_exec,
    "eval": _act_eval,
    "screenshot": _act_screenshot,
    "scene": _act_scene,
}


# --------------------------------------------------------------------------
# Bombeo en el hilo principal
# --------------------------------------------------------------------------

def _pump():
    """Timer del hilo principal: atiende las peticiones encoladas."""
    while True:
        try:
            req, slot, done = _requests.get_nowait()
        except queue.Empty:
            break
        try:
            fn = ACTIONS.get(req.get("action"))
            if fn is None:
                slot["response"] = {"ok": False,
                                    "error": "accion desconocida: %r" % req.get("action")}
            else:
                slot["response"] = fn(req)
            _state["last"] = req.get("action", "?")
        except Exception:
            slot["response"] = {"ok": False, "traceback": traceback.format_exc()}
        finally:
            done.set()
    return 0.05  # siguiente pasada en 50 ms


# --------------------------------------------------------------------------
# Servidor
# --------------------------------------------------------------------------

class BridgeServer(threading.Thread):
    daemon = True

    def __init__(self, port):
        super().__init__(name="ClaudeBridge")
        self.port = port
        self.token = _token()
        self._stop = threading.Event()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((HOST, port))
        self.sock.listen(4)
        self.sock.settimeout(0.5)

    def shutdown(self):
        self._stop.set()
        try:
            self.sock.close()
        except Exception:
            pass

    def run(self):
        while not self._stop.is_set():
            try:
                conn, _ = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        with conn:
            conn.settimeout(300)
            try:
                buf = b""
                while not buf.endswith(b"\n"):
                    chunk = conn.recv(65536)
                    if not chunk:
                        return
                    buf += chunk
                req = json.loads(buf.decode("utf-8"))

                if req.get("token") != self.token:
                    conn.sendall(b'{"ok": false, "error": "token invalido"}\n')
                    return

                # Encolar para el hilo principal y esperar el resultado.
                slot, done = {}, threading.Event()
                _requests.put((req, slot, done))
                if not done.wait(timeout=req.get("timeout", 280)):
                    resp = {"ok": False, "error": "timeout esperando al hilo principal "
                                                  "(Blender ocupado o el timer no corre)"}
                else:
                    resp = slot.get("response", {"ok": False, "error": "sin respuesta"})
            except Exception:
                resp = {"ok": False, "traceback": traceback.format_exc()}
            try:
                conn.sendall((json.dumps(resp) + "\n").encode("utf-8"))
            except Exception:
                pass


def start(port=PORT):
    global _server
    if _state["running"]:
        return "ya estaba activo en el puerto %s" % _state["port"]
    _server = BridgeServer(port)
    _server.start()
    if not bpy.app.timers.is_registered(_pump):
        bpy.app.timers.register(_pump, persistent=True)
    _state.update(running=True, port=port)
    os.makedirs(BASE_DIR, exist_ok=True)
    with open(os.path.join(BASE_DIR, ".port"), "w") as fh:
        fh.write(str(port))
    print("[Claude Bridge] escuchando en %s:%s" % (HOST, port))
    return "activo en %s:%s" % (HOST, port)


def stop():
    global _server
    if _server is not None:
        _server.shutdown()
        _server = None
    if bpy.app.timers.is_registered(_pump):
        bpy.app.timers.unregister(_pump)
    _state.update(running=False, port=None)
    print("[Claude Bridge] detenido")


# --------------------------------------------------------------------------
# UI
# --------------------------------------------------------------------------

class CLAUDE_OT_toggle(bpy.types.Operator):
    bl_idname = "claude.toggle_bridge"
    bl_label = "Alternar Claude Bridge"
    bl_description = "Arranca o detiene el servidor local"

    def execute(self, context):
        if _state["running"]:
            stop()
            self.report({'INFO'}, "Claude Bridge detenido")
        else:
            self.report({'INFO'}, start())
        return {'FINISHED'}


class CLAUDE_PT_panel(bpy.types.Panel):
    bl_idname = "CLAUDE_PT_panel"
    bl_label = "Claude Bridge"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Claude"

    def draw(self, context):
        col = self.layout.column(align=True)
        if _state["running"]:
            col.label(text="Activo en %s:%s" % (HOST, _state["port"]), icon='LINKED')
            col.label(text="Ultima accion: %s" % _state["last"])
            col.operator("claude.toggle_bridge", text="Detener", icon='PAUSE')
        else:
            col.label(text="Detenido", icon='UNLINKED')
            col.operator("claude.toggle_bridge", text="Arrancar", icon='PLAY')


_classes = (CLAUDE_OT_toggle, CLAUDE_PT_panel)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    start()


def unregister():
    stop()
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)


# Permite ejecutarlo directamente desde el Editor de Texto de Blender,
# sin instalarlo ni reiniciar.
if __name__ == "__main__":
    try:
        unregister()
    except Exception:
        pass
    register()
