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

LA HORA: 22:45 de Madrid
Los futuros del VIX liquidan a las 16:15 de Nueva York = 22:15 de Madrid, y la
sesion SIGUIENTE abre a las 17:00 de Nueva York = 23:00 de Madrid. La ventana
util son esos 45 minutos: antes no existe el precio oficial, despues ya hay una
barra recien nacida del dia siguiente que se podria colar. La diferencia entre
husos son 6 horas todo el ano (los dos cambian a la vez), asi que 22:45 vale en
verano y en invierno.
El Master Daily corre a las 15:50 de Madrid = 09:50 de Nueva York: a esa hora el
settlement del dia ni existe. Por eso esto NO cuelga de el -- y ademas un fallo
del VIX no debe tumbar a sus otros 8 hijos.

DIAS SIN MERCADO
No se hace nada. Y si es dia habil pero la serie no avanza, no se publica pero
SI se avisa: callarse ahi seria saltarse en silencio justo los dias malos.

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
import descargar_futuros_vix as dfv

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


def es_dia_de_mercado(hoy=None):
    """True si hoy hay sesion en el CFE. Si el calendario no esta disponible se
    cae a "de lunes a viernes": cubre el 96% de los casos y nunca calla un dia
    habil, que es el error que importa."""
    import datetime as _dt
    import pandas as pd
    d = pd.Timestamp(hoy or _dt.date.today())
    hab = dfv._habiles_cfe()
    if hab:
        return d.normalize() in hab
    return d.weekday() < 5


def ultima_fecha_serie():
    """Ultima fecha de la serie en disco, o None si no hay serie todavia."""
    import pandas as pd
    f = os.path.join(HERE, "data", "vix_futuros_M1_M8.csv")
    if not os.path.exists(f):
        return None
    try:
        return pd.read_csv(f, usecols=["Fecha"])["Fecha"].max()
    except Exception:
        return None


def publicar_estado():
    """Copia data/estado.json a la raiz del repo publico.

    Es lo que permite vigilar el sistema DESDE FUERA de esta maquina: un proceso
    que corre en GitHub puede mirar cuando fue la ultima corrida buena. Un
    vigilante que vive en la misma maquina que el vigilado no es redundancia --
    lo que mata a uno mata al otro."""
    import shutil
    src = os.path.join(HERE, "data", "estado.json")
    if os.path.exists(src):
        shutil.copy(src, os.path.join(SITIO, "estado.json"))


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
        if not dry and not es_dia_de_mercado():
            registrar("hoy no hay sesion en el CFE: no se hace nada")
            registrar("CORRIDA OMITIDA (dia sin mercado)")
            return

        antes = ultima_fecha_serie()
        paso("descarga", [PYTHON, "descargar_futuros_vix.py"], dry=dry)
        despues = ultima_fecha_serie()

        # Dia habil y la serie NO ha avanzado: no se publica, pero NO se calla.
        # Callarse aqui seria saltarse en silencio justo los dias en los que algo
        # va mal, que es la enfermedad contra la que existe todo este fichero.
        if not dry and antes is not None and despues == antes:
            registrar("LA SERIE NO HA AVANZADO (sigue en %s) en un dia de mercado"
                      % despues)
            estado.escribir(False, "correr_noche/sin_avance",
                            "Dia de mercado y la serie sigue en %s." % despues)
            publicar_estado()
            estado.avisar("<b>VIX CURVE - SIN DATO NUEVO</b>\n\n"
                          "Hoy hay mercado pero la serie sigue en <b>%s</b>. "
                          "No se ha publicado nada.\n"
                          "Puede ser un festivo que el calendario no recoge, o que "
                          "el proveedor no tenga el dato todavia." % despues)
            soltar_candado()
            sys.exit(1)

        paso("sitio", [PYTHON, "construir_sitio.py"], dry=dry)
        if not dry:
            publicar_estado()

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
