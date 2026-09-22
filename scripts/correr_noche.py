"""
correr_noche.py -- la corrida diaria completa, desatendida.

POR QUE EXISTE
Hasta hoy el sistema era una foto fija: 0 tareas programadas, 0 hijos del Master
Daily, y la serie solo avanzaba cuando alguien la lanzaba a mano. Eso es
exactamente como murio el Google Sheet anterior -- tres meses congelado con la
pagina publica sirviendo datos viejos sin avisar de nada.

LA CADENA
  1. descargar_futuros_vix.py   baja los contratos vivos y rehace la serie
  2. construir_sitio.py          recalcula percentiles y regenera el panel
  3. git push                    publica en GitHub Pages
  4. radar_telegram.py --enviar  manda el mensaje del dia

Si un paso falla, los siguientes NO corren: mas vale una pagina con el dato de
ayer y un aviso en el movil, que una pagina con un dato inventado y silencio.

LA HORA
Se programa a las 23:00 de Madrid. Los futuros del VIX liquidan a las 16:15 de
Nueva York = 22:15 de Madrid, y la diferencia son 6 horas todo el ano (los dos
husos cambian a la vez), asi que 23:00 vale en verano y en invierno. El Master
Daily corre a las 15:50 de Madrid = 09:50 de Nueva York: a esa hora el
settlement del dia ni existe. Por eso esto NO cuelga de el.

CREDENCIALES
No hay que configurar nada: TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID ya estan en
las variables de entorno del usuario, y una tarea programada que corra como ese
usuario las hereda.

USO
  python correr_noche.py              # la cadena entera
  python correr_noche.py --dry-run    # ensena que haria, sin tocar nada
  python correr_noche.py --sin-push   # todo menos publicar
  python correr_noche.py --sin-aviso  # todo menos el Telegram
  python correr_noche.py --once       # ignora el candado (lanzamiento manual)

Reglas tecnicas del proyecto: ASCII, cp1252.
"""

import os
import io
import sys
import json
import time
import subprocess
import datetime as dt

import estado

HERE = os.path.dirname(os.path.abspath(__file__))
SITIO = os.path.join(HERE, "sitio")
LOG = os.path.join(HERE, "data", "correr_noche.log")
CANDADO = os.path.join(HERE, "data", ".corriendo")
PYTHON = sys.executable
CANDADO_VIEJO_H = 3     # un candado mas viejo que esto es un proceso muerto


def registrar(msg):
    linea = "%s  %s" % (dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(linea)
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with io.open(LOG, "a", encoding="utf-8") as fh:
        fh.write(linea + "\n")


def tomar_candado(forzar=False):
    """Evita dos corridas solapadas. Un candado rancio no bloquea para siempre:
    un proceso que murio a medias no puede dejar el sistema parado sin remedio."""
    if os.path.exists(CANDADO):
        edad = (time.time() - os.path.getmtime(CANDADO)) / 3600.0
        if edad > CANDADO_VIEJO_H:
            registrar("candado de hace %.1f h: se da por muerto y se retira" % edad)
            os.remove(CANDADO)
        elif forzar:
            registrar("candado presente pero --once: se ignora")
        else:
            registrar("YA HAY UNA CORRIDA EN MARCHA (candado de hace %.0f min). Salgo."
                      % (edad * 60))
            return False
    with io.open(CANDADO, "w", encoding="utf-8") as fh:
        fh.write(str(os.getpid()))
    return True


def soltar_candado():
    if os.path.exists(CANDADO):
        os.remove(CANDADO)


def paso(nombre, cmd, cwd=None, dry=False):
    """Un eslabon. Devuelve True si fue bien; si no, PARA todo."""
    registrar("--> %s" % nombre)
    if dry:
        registrar("    (dry-run) %s" % " ".join(cmd))
        return True
    t0 = time.time()
    p = subprocess.run(cmd, cwd=cwd or HERE, capture_output=True, text=True)
    dur = time.time() - t0
    cola = (p.stdout or "").strip().splitlines()[-3:]
    for l in cola:
        registrar("    %s" % l[:160])
    if p.returncode != 0:
        err = ((p.stderr or "") + (p.stdout or "")).strip()[-400:]
        registrar("    FALLO (codigo %d, %.0f s)" % (p.returncode, dur))
        soltar_candado()
        estado.fallar("correr_noche/%s" % nombre,
                      "El paso '%s' devolvio %d.\n%s" % (nombre, p.returncode, err))
    registrar("    OK (%.0f s)" % dur)
    return True


def hay_cambios():
    p = subprocess.run(["git", "status", "--porcelain"], cwd=SITIO,
                       capture_output=True, text=True)
    return bool((p.stdout or "").strip())


def publicar(dry=False):
    registrar("--> publicar en GitHub Pages")
    if dry:
        registrar("    (dry-run) git add -A / commit / push")
        return True
    if not hay_cambios():
        registrar("    nada que publicar (la pagina ya esta al dia)")
        return True
    msg = "actualizacion diaria %s" % dt.date.today().isoformat()
    for cmd in (["git", "add", "-A"], ["git", "commit", "-m", msg], ["git", "push"]):
        p = subprocess.run(cmd, cwd=SITIO, capture_output=True, text=True)
        if p.returncode != 0:
            sal = ((p.stdout or "") + (p.stderr or "")).strip()
            registrar("    git fallo: %s" % sal[:200])
            soltar_candado()
            estado.fallar("correr_noche/publicar",
                          "git %s devolvio %d.\n%s" % (cmd[1], p.returncode, sal[:400]))
    registrar("    publicado")
    return True


def main():
    args = sys.argv[1:]
    dry = "--dry-run" in args

    registrar("=" * 62)
    registrar("CORRIDA NOCTURNA VIX_CURVE%s" % ("  (DRY-RUN)" if dry else ""))

    if not dry and not tomar_candado(forzar="--once" in args):
        return

    try:
        paso("descarga", [PYTHON, "descargar_futuros_vix.py"], dry=dry)
        paso("sitio", [PYTHON, "construir_sitio.py"], dry=dry)

        if "--sin-push" in args:
            registrar("--> publicar: SALTADO (--sin-push)")
        else:
            publicar(dry=dry)

        if "--sin-aviso" in args:
            registrar("--> aviso: SALTADO (--sin-aviso)")
        else:
            paso("aviso", [PYTHON, "radar_telegram.py", "--enviar"], dry=dry)

        if not dry:
            estado.escribir(True, "correr_noche", "cadena completa")
        registrar("CORRIDA COMPLETA")
    finally:
        if not dry:
            soltar_candado()


if __name__ == "__main__":
    main()
