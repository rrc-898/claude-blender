# claude-blender — puente con la sesión viva de Blender

Add-on + cliente de línea de comandos para que un agente (Claude Code) pueda
**inspeccionar y modificar la sesión GUI de Blender que ya tienes abierta**, en
vez de trabajar a ciegas sobre archivos `.blend`.

## Cómo funciona

`bpy` sólo existe dentro del proceso de Blender, así que el add-on levanta un
servidor TCP local dentro de él:

- Escucha en `127.0.0.1:51707` y exige un token compartido
  (`~/claude-blender/.token`, modo `600`, se genera solo la primera vez).
- El hilo del socket sólo **encola** peticiones; un `bpy.app.timers` las ejecuta
  en el hilo principal, porque la API de Blender no es thread-safe.
- Cada `exec` hace `undo_push`: lo que cambie el agente se revierte con `Ctrl+Z`.

## Ficheros

| Fichero | Qué es |
|---|---|
| `claude_bridge.py` | El add-on. Panel en `View3D > barra lateral (N) > Claude`. |
| `cb.py` | Cliente: `ping`, `scene`, `eval`, `exec`, `shot`. |
| `bl.py` | Cliente mínimo, para pasar parámetros sueltos como `clave=valor`. |
| `preview.py` | Render rápido de Cycles para evaluar iluminación, restaurando los ajustes. |

## Instalación

```bash
git clone https://github.com/rrc-898/claude-blender ~/claude-blender
# enlaza el add-on en la carpeta de add-ons de tu Blender, p. ej. (flatpak, 5.2):
mkdir -p ~/.var/app/org.blender.Blender/config/blender/5.2/scripts/addons
ln -s ~/claude-blender/claude_bridge.py \
      ~/.var/app/org.blender.Blender/config/blender/5.2/scripts/addons/
```

Actívalo en `Preferencias > Add-ons > Claude Bridge` y arráncalo desde el panel.
Los scripts dan por hecho que el repo vive en `~/claude-blender`.

## Uso

```bash
python3 cb.py ping                         # ¿está vivo el puente?
python3 cb.py scene                        # resumen de la escena
python3 cb.py eval "bpy.context.object.name"
python3 cb.py exec script.py               # o  cb.py exec -  para leer de stdin
python3 cb.py shot viewport                # window | area | viewport
{ echo "CB_SAMPLES=256; CB_SCALE=75"; cat preview.py; } | python3 cb.py exec -
```

Las capturas van a `~/claude-blender/shots/`, reescaladas a 1600 px de lado mayor.

## Seguridad

El puente ejecuta Python arbitrario dentro de Blender para quien tenga el token.
Sólo escucha en loopback; no lo expongas a la red ni subas `.token` a ningún sitio
(está en `.gitignore`).

Hermano de [claude-resolve](https://github.com/rrc-898/claude-resolve), el mismo
concepto para DaVinci Resolve.
