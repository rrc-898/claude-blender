"""Render rapido de Cycles para evaluar iluminacion, restaurando los ajustes.

Corre dentro de Blender via el puente. Para cambiar parametros, antepon
asignaciones al enviarlo, p.ej.:

    { echo "CB_SAMPLES=256; CB_SCALE=75"; cat preview.py; } | cb.py exec -
"""
import bpy
import os
import time

SAMPLES = globals().get("CB_SAMPLES", 128)
SCALE = globals().get("CB_SCALE", 50)
OUT = globals().get("CB_OUT", os.path.expanduser("~/claude-blender/shots/preview.png"))

scn = bpy.context.scene
r, c = scn.render, scn.cycles

prev = {
    "samples": c.samples,
    "pct": r.resolution_percentage,
    "path": r.filepath,
    "fmt": r.image_settings.file_format,
    "denoise": c.use_denoising,
}

# Preferir GPU si esta configurada.
try:
    prefs = bpy.context.preferences.addons["cycles"].preferences
    devs = [d.name for d in prefs.devices if d.use and d.type != 'CPU']
    print("device:", c.device, "| compute:", prefs.compute_device_type,
          "| GPUs activas:", devs or "ninguna")
except Exception as e:
    print("device: (no consultable)", e)

try:
    c.samples = SAMPLES
    c.use_denoising = True
    r.resolution_percentage = SCALE
    r.image_settings.file_format = 'PNG'
    r.filepath = OUT
    t0 = time.time()
    bpy.ops.render.render(write_still=True)
    print("render %dx%d  %d samples  %.1fs -> %s" % (
        r.resolution_x * SCALE // 100, r.resolution_y * SCALE // 100,
        SAMPLES, time.time() - t0, OUT))
finally:
    c.samples = prev["samples"]
    c.use_denoising = prev["denoise"]
    r.resolution_percentage = prev["pct"]
    r.filepath = prev["path"]
    r.image_settings.file_format = prev["fmt"]
