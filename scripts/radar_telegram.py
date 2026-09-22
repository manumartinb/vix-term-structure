"""
radar_telegram.py -- el mensaje diario del radar de la curva de futuros VIX.

QUE MANDA
Un mensaje que EMPIEZA POR LO QUE HA CAMBIADO (extremos, rachas, movimientos fuertes)
y solo despues ensena la parrilla de las 28 parejas. Ese orden es deliberado: un
boletin diario que empieza siempre por la misma tabla se acaba silenciando.

Se apoya en:
  descargar_futuros_vix.py -> data/vix_futuros_M1_M8.csv  (la serie)
  percentiles_curva.py     -> las 28 parejas y la convencion de percentil

CONVENCION (heredada del VIX Studio para no cambiar la lectura):
  100 = backwardation extrema (curva del reves)   0 = contango extremo

FORMATO
Bloque <pre> monoespaciado, NO tabla rica: la nota del usuario sobre la Bot API 10.1
avisa de que las tablas de mas de 4-5 columnas scrollean en movil, y el triangulo
tiene 8. En <pre> cabe en ~30 caracteres y se ve igual en cualquier cliente.

CREDENCIALES (nunca en el codigo)
  1) variables de entorno TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID, o
  2) fichero telegram_config.json junto a este script: {"token": "...", "chat_id": "..."}
     (anadirlo al .gitignore)

USO
  python radar_telegram.py                  # imprime el mensaje, NO envia (por defecto)
  python radar_telegram.py --enviar         # lo manda a Telegram
  python radar_telegram.py --fecha 2020-03-16   # reconstruye el de un dia concreto

Reglas tecnicas del proyecto: ASCII, cp1252.
"""

import os
import sys
import json
import datetime as dt

import numpy as np
import pandas as pd

import percentiles_curva as pcv

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "telegram_config.json")

# umbrales heredados del Apps Script del VIX Studio
UMBRAL_ALTO = 95.0
UMBRAL_BAJO = 5.0
# para las rachas se usa un listón mas bajo: lo interesante no es solo el extremo
# puntual, es llevar semanas apoyado en una banda
RACHA_ALTO = 90.0
RACHA_BAJO = 10.0
RACHA_MINIMA = 3           # dias seguidos para mencionarla
SALTO_NOTABLE = 12.0       # puntos de cambio contra el dia anterior
DIAS_RANCIO = 4            # si el ultimo dato es mas viejo, se avisa
DIAS_HISTORIA_RACHA = 120  # cuantos dias hacia atras se recalculan para las rachas
MAX_DETALLE = 5            # mas de esto en la misma categoria -> se resume en 1 linea
N_PAREJAS = 28


def serie_percentiles(r, n_dias):
    """Percentiles (0-100) de las 28 parejas en los ultimos n_dias.

    Delega en la libreria compartida: el calculo vive SOLO en percentiles_curva.py.
    Antes esta funcion tenia su propia copia con el percentil GLOBAL mientras la web
    ya usaba el condicional, y publicaban numeros distintos el mismo dia (7,1 puntos
    de media). Ese es justamente el bug que este cambio cierra."""
    pct = pcv.matriz_percentiles(r)
    return pct.tail(int(n_dias))


def racha(serie_col, alto=True):
    """Cuantos dias seguidos, contando desde el ultimo, lleva por encima/debajo."""
    lim = RACHA_ALTO if alto else RACHA_BAJO
    n = 0
    for v in reversed(serie_col.dropna().values):
        if (alto and v >= lim) or ((not alto) and v <= lim):
            n += 1
        else:
            break
    return n


def _resumir(etiqueta, items, plantilla, resumen, tope=MAX_DETALLE):
    """Si son pocos los lista uno a uno; si son muchos, una sola linea que los agrupa.

    Sin esto, un dia como el 16-mar-2020 (24 de las 28 parejas en extremo a la vez)
    produce 24 lineas identicas y el mensaje se vuelve ilegible: justo el muro de
    texto que hace que dejes de abrirlo. Medido y corregido el 2026-09-21."""
    if not items:
        return []
    if len(items) <= tope:
        return [plantilla % (etiqueta, c, round(v)) for c, v in items]
    return ["%s  %d de %d parejas  %s" % (etiqueta, len(items), N_PAREJAS, resumen)]


def titular(pcts, hist_pcts):
    """Las lineas de 'que ha cambiado'. Lista de strings; vacia si no hay nada."""
    lineas = []

    # 1. extremos de hoy (los que dispararian alerta)
    altos = sorted([(c, v) for c, v in pcts.items() if v >= UMBRAL_ALTO], key=lambda x: -x[1])
    bajos = sorted([(c, v) for c, v in pcts.items() if v <= UMBRAL_BAJO], key=lambda x: x[1])
    lineas += _resumir("EXTREMO ALTO", altos, "%s  %s en %d",
                       "por encima de %d: curva del reves generalizada" % UMBRAL_ALTO)
    lineas += _resumir("EXTREMO BAJO", bajos, "%s  %s en %d",
                       "por debajo de %d: contango generalizado" % UMBRAL_BAJO)

    # 2. rachas
    if hist_pcts is not None and len(hist_pcts) > RACHA_MINIMA:
        for alto in (True, False):
            rr = []
            for c in hist_pcts.columns:
                n = racha(hist_pcts[c], alto=alto)
                if n >= RACHA_MINIMA:
                    rr.append((c, n))
            rr.sort(key=lambda x: -x[1])
            lado = "por encima" if alto else "por debajo"
            lim = int(RACHA_ALTO if alto else RACHA_BAJO)
            if not rr:
                continue
            if len(rr) <= MAX_DETALLE:
                lineas += ["RACHA         %s lleva %d dias %s de %d" % (c, n, lado, lim)
                           for c, n in rr]
            else:
                lineas.append("RACHA         %d parejas llevan %s de %d "
                              "(la mas larga %s, %d dias)"
                              % (len(rr), lado, lim, rr[0][0], rr[0][1]))

    # 3. saltos fuertes contra ayer
    if hist_pcts is not None and len(hist_pcts) >= 2:
        ayer = hist_pcts.iloc[-2]
        saltos = []
        for c in hist_pcts.columns:
            if c in pcts and not np.isnan(ayer.get(c, np.nan)):
                d = pcts[c] - ayer[c]
                if abs(d) >= SALTO_NOTABLE:
                    saltos.append((c, d, ayer[c], pcts[c]))
        saltos.sort(key=lambda x: -abs(x[1]))
        if len(saltos) <= MAX_DETALLE:
            lineas += ["SALTO         %s %+d puntos en un dia (%d -> %d)"
                       % (c, round(d), round(a), round(h)) for c, d, a, h in saltos]
        elif saltos:
            c, d, a, h = saltos[0]
            lineas.append("SALTO         %d parejas se mueven mas de %d puntos "
                          "(la mayor %s, %+d)"
                          % (len(saltos), int(SALTO_NOTABLE), c, round(d)))
    return lineas


def construir_mensaje(fecha=None):
    serie = pcv.cargar_serie()
    r = pcv.ratios(serie)

    f, pcts, n_hist, _ = pcv.triangulo(serie, fecha=fecha)
    pcts = pcts.dropna()

    r_hasta = r[r.index <= f]
    hist_pcts = serie_percentiles(r_hasta, DIAS_HISTORIA_RACHA)

    partes = []
    partes.append("<b>RADAR CURVA VIX</b> - %s" % f.strftime("%d %b %Y"))

    # aviso de frescura: solo tiene sentido en el mensaje del dia. Al reconstruir un
    # dia del pasado con --fecha, el dato es viejo A PROPOSITO y avisar seria ruido.
    atraso = (pd.Timestamp(dt.date.today()) - f).days
    if fecha is None and atraso > DIAS_RANCIO:
        partes.append("")
        partes.append("<b>AVISO: el ultimo dato es de hace %d dias.</b> "
                      "Puede que la descarga no este corriendo." % atraso)

    lineas = titular(pcts, hist_pcts)
    partes.append("")
    if lineas:
        partes.extend(lineas)
    else:
        partes.append("Sin extremos ni movimientos fuertes. Maximo %s en %d, minimo %s en %d."
                      % (pcts.idxmax(), round(pcts.max()),
                         pcts.idxmin(), round(pcts.min())))

    partes.append("")
    partes.append("<pre>" + pcv.pintar(pcts) + "</pre>")

    curva = serie.loc[f].dropna()
    partes.append("Curva: " + "  ".join("%.4g" % v for v in curva.values))
    partes.append("Historia: %d dias%s. 100 = backwardation, 0 = contango."
                  % (n_hist, " desde abr-2007"))
    return "\n".join(partes)


def credenciales():
    tok = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if tok and chat:
        return tok, chat
    if os.path.exists(CONFIG):
        with open(CONFIG, "r") as fh:
            c = json.load(fh)
        return c.get("token"), c.get("chat_id")
    return None, None


def enviar(texto):
    import urllib.request
    tok, chat = credenciales()
    if not tok or not chat:
        print("SIN CREDENCIALES. Pon TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID en el entorno,")
        print("o crea %s con {\"token\": \"...\", \"chat_id\": \"...\"}" % CONFIG)
        return False
    payload = json.dumps({"chat_id": chat, "text": texto,
                          "parse_mode": "HTML"}).encode("utf-8")
    req = urllib.request.Request(
        "https://api.telegram.org/bot%s/sendMessage" % tok,
        data=payload, headers={"Content-Type": "application/json"})
    try:
        resp = json.loads(urllib.request.urlopen(req, timeout=30).read().decode())
        if resp.get("ok"):
            print("Enviado (message_id %s)" % resp["result"]["message_id"])
            return True
        print("Telegram respondio: %s" % resp)
    except Exception as e:
        print("ERROR al enviar: %s" % str(e)[:200])
    return False


def main():
    args = sys.argv[1:]

    def opt(nombre, cast=str):
        if nombre in args:
            return cast(args[args.index(nombre) + 1])
        return None

    texto = construir_mensaje(fecha=opt("--fecha"))
    if "--enviar" in args:
        enviar(texto)
    else:
        print(texto)
        print()
        print("-" * 60)
        print("(no enviado: anade --enviar para mandarlo a Telegram)")


if __name__ == "__main__":
    main()
