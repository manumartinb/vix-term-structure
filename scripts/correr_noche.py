"""
correr_noche.py -- la corrida diaria completa, desatendida.
(El nombre es historico: desde el 2026-09-23 corre por la MANANA. Ver LA HORA.)

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

LA HORA: 08:00 de Madrid, con reintento a las 11:00 (desde el 2026-09-23)
La historia sale SOLO del fichero oficial del CBOE, y el CBOE lo actualiza de
madrugada: medido el 2026-09-23, Last-Modified 05:06 GMT = 07:06 de Madrid, con la
sesion del dia anterior. A las 08:00 hay casi una hora de margen; la corrida de
las 11:00 es la red por si un dia lo cuelgan tarde.
Antes corria a las 22:45 contra TradingView, con una premisa FALSA escrita aqui
mismo: "la diferencia entre husos son 6 horas todo el ano, los dos cambian a la
vez". No cambian a la vez: EEUU adelanta la hora ~3 semanas antes que Europa y la
atrasa 1 semana despues. En esas semanas TradingView fechaba cada vela con el dia
anterior y el panel guardaba el precio de MANANA (estudio completo en
ESTRATEGIAS/ANALISIS/SETTLE_OFICIAL_VIX_20260923).
El Master Daily corre a las 15:50 de Madrid: esto NO cuelga de el -- un fallo del
VIX no debe tumbar a sus otros hijos.

QUE SESION TOCA
La ultima sesion del CFE ANTERIOR a hoy (la de ayer; el lunes, la del viernes).
  - Si la serie ya llega ahi: no hay nada que hacer y se sale sin ruido. Pasa los
    domingos, los lunes y cualquier dia siguiente a un festivo.
  - Si el CBOE aun no la ha colgado y NO es el ultimo intento: se deja constancia y
    se sale sin alarma; la corrida de las 11:00 lo vuelve a probar.
  - Si en el ULTIMO intento (desde las 11:00) sigue sin estar: no se publica, pero
    SI se avisa. Callarse ahi seria saltarse en silencio justo los dias malos.

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
  python correr_noche.py --ultimo     # trata esta corrida como el ultimo intento
  python correr_noche.py --primer-intento   # lo contrario, aunque sean mas de las 11 (pruebas)
  python correr_noche.py --hoy 2026-09-28   # simula otra fecha (solo pruebas)

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
HORA_ULTIMO_INTENTO = 11   # la corrida de las 11:00 (o cualquiera posterior) es la
                           # ultima del dia: si ahi falta el dato, se avisa


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


def es_sesion(d):
    """True si la fecha es sesion del CFE. Si el calendario no esta disponible se
    cae a "de lunes a viernes": cubre el 96% de los casos y nunca calla un dia
    habil, que es el error que importa."""
    import pandas as pd
    d = pd.Timestamp(d).normalize()
    hab = dfv._habiles_cfe()
    if hab:
        return d in hab
    return d.weekday() < 5


def sesion_objetivo(hoy=None):
    """La sesion que YA deberia estar publicada: la ultima sesion del CFE ANTERIOR
    a hoy. El CBOE cuelga la liquidacion de madrugada del dia siguiente, asi que
    la de hoy nunca esta todavia."""
    import pandas as pd
    d = pd.Timestamp(hoy or dt.date.today()).normalize()
    for k in range(1, 15):
        c = d - pd.Timedelta(days=k)
        if es_sesion(c):
            return c
    return d - pd.Timedelta(days=1)


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


def publicar(dry=False, sesion=None):
    registrar("--> publicar en GitHub Pages")
    if dry:
        registrar("    (dry-run) git add -A / commit / push")
        return True
    if not hay_cambios():
        registrar("    nada que publicar (la pagina ya esta al dia)")
        return True
    # el mensaje lleva la SESION publicada, no el dia en que corre: por la manana
    # no coinciden, y el commit tiene que decir que dato trae
    msg = "actualizacion diaria: sesion %s" % (sesion or dt.date.today().isoformat())
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
    import pandas as pd
    args = sys.argv[1:]
    dry = "--dry-run" in args
    hoy = args[args.index("--hoy") + 1] if "--hoy" in args else None
    ultimo = "--ultimo" in args or (dt.datetime.now().hour >= HORA_ULTIMO_INTENTO
                                    and "--primer-intento" not in args)

    registrar("=" * 62)
    registrar("CORRIDA DIARIA VIX_CURVE%s%s%s"
              % ("  (DRY-RUN)" if dry else "", "  [ultimo intento]" if ultimo else "",
                 "  [hoy simulado: %s]" % hoy if hoy else ""))

    if not dry and not tomar_candado(forzar="--once" in args):
        return

    try:
        objetivo = sesion_objetivo(hoy)
        antes = ultima_fecha_serie()
        registrar("sesion que toca: %s | la serie llega al %s" % (objetivo.date(), antes))
        if antes is not None and pd.Timestamp(antes) >= objetivo:
            registrar("la serie ya esta al dia: nada que hacer")
            registrar("CORRIDA OMITIDA (ya al dia)")
            return

        paso("descarga", [PYTHON, "descargar_futuros_vix.py"], dry=dry)
        despues = ultima_fecha_serie()

        if not dry and (despues is None or pd.Timestamp(despues) < objetivo):
            avanzo = antes is None or despues != antes
            if not avanzo and not ultimo:
                # El CBOE aun no ha colgado la sesion: normal si un dia va tarde.
                # Constancia en disco y sin alarma; la corrida de las 11:00 reintenta.
                registrar("el CBOE aun no ha colgado la sesion del %s (la serie sigue "
                          "en %s): se reintenta a las %02d:00"
                          % (objetivo.date(), despues, HORA_ULTIMO_INTENTO))
                estado.escribir(False, "correr_noche/pendiente",
                                "La sesion del %s aun no esta en el CBOE; la serie sigue "
                                "en %s. Se reintenta a las %02d:00."
                                % (objetivo.date(), despues, HORA_ULTIMO_INTENTO))
                registrar("CORRIDA APLAZADA (esperando al CBOE)")
                return
            if not avanzo:
                # Ultimo intento y sigue sin estar: no se publica, pero NO se calla.
                # Callarse aqui seria saltarse en silencio justo los dias en los que
                # algo va mal, que es la enfermedad contra la que existe este fichero.
                registrar("LA SERIE NO HA AVANZADO (sigue en %s) y la sesion del %s "
                          "ya deberia estar publicada" % (despues, objetivo.date()))
                estado.escribir(False, "correr_noche/sin_avance",
                                "La sesion del %s no esta en el CBOE en el ultimo "
                                "intento; la serie sigue en %s." % (objetivo.date(), despues))
                publicar_estado()
                estado.avisar("<b>VIX CURVE - SIN DATO NUEVO</b>\n\n"
                              "El CBOE no ha publicado la liquidacion del <b>%s</b> "
                              "(ultimo intento de la manana). La serie sigue en <b>%s</b> "
                              "y no se ha publicado nada.\n"
                              "Puede ser un festivo que el calendario no recoge, o que "
                              "el CBOE vaya con retraso." % (objetivo.date(), despues))
                soltar_candado()
                sys.exit(1)
            # Avanzo, pero no hasta la sesion que tocaba: el CBOE se ha saltado una
            # o el calendario cuenta como sesion un dia que no lo fue. Se publica lo
            # que hay y queda escrito.
            registrar("AVISO: la serie avanza al %s pero la sesion que tocaba era el %s"
                      % (despues, objetivo.date()))

        paso("sitio", [PYTHON, "construir_sitio.py"], dry=dry)
        if not dry:
            publicar_estado()

        if "--sin-push" in args:
            registrar("--> publicar: SALTADO (--sin-push)")
        else:
            publicar(dry=dry, sesion=despues)

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
