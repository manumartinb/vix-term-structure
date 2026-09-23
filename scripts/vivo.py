"""
vivo.py -- la foto INTRADIA de la curva, percentileada contra la historia cerrada.

QUE HACE
Pide los 8 contratos vivos EN UNA SOLA VENTANA de pocos segundos, calcula las 28
parejas de ese instante y las situa contra el historico de dias ANTERIORES. El
resultado es PROVISIONAL: a la manana siguiente entra la liquidacion oficial del
CBOE y esa es la que pasa a ser historia. El dato en vivo nunca se mete en la vara.
Por eso este es el UNICO sitio del sistema que sigue usando TradingView: sus velas
de 5 minutos se juzgan por antiguedad (hora local contra hora local), no por fecha,
y no les afecta el desfase de las velas DIARIAS que obligo a sacar TradingView de la
historia el 2026-09-23.

POR QUE DE UNA EN UNA Y NO EN PARALELO (medido el 2026-09-22)
Con 8 peticiones simultaneas sobre la misma conexion de TradingView, 2 de las 8
devolvieron "sin datos" en medio segundo. No era que faltara el precio: era la
tuberia saturada. Secuencial con reintento: 8 de 8 en 4,4 s, sin necesitar el
reintento ni una vez. Un hueco inventado es peor que tardar 4 segundos, porque
se lee como si el contrato no cotizara.

ANTIGUEDAD, NO FECHA
Cada pata se juzga por lo vieja que es su ultima vela, no por su fecha: asi no
hay que asumir en que huso viene el timestamp del proveedor. Si pasa de
MAX_EDAD_MIN, la pata se da por rancia y se sustituye por el ultimo settlement
cerrado, avisando de ello. Una pareja con alguna pata rancia se publica, pero
NO dispara alerta.

SALIDA
  sitio/vivo.json   lo que lee la banda EN VIVO del panel

USO
  python vivo.py                 # pide, calcula e imprime; escribe vivo.json
  python vivo.py --dry-run       # igual pero sin escribir nada
  python vivo.py --push          # ademas publica en GitHub Pages
  python vivo.py --enviar        # ademas avisa por Telegram SI hay extremo

Reglas tecnicas del proyecto: ASCII, cp1252.
"""

import os
import io
import sys
import json
import time
import subprocess
import datetime as dt

import numpy as np
import pandas as pd

import estado
import percentiles_curva as pcv
import descargar_futuros_vix as dfv

HERE = os.path.dirname(os.path.abspath(__file__))
SITIO = os.path.join(HERE, "sitio")
VIVO_JSON = os.path.join(SITIO, "vivo.json")

MAX_EDAD_MIN = 120     # mas viejo que esto -> pata rancia, se usa el cierre previo
REINTENTOS = 3
PAUSA_REINTENTO = 0.8
UMBRAL_ALTO = 95.0     # los mismos que el radar nocturno
UMBRAL_BAJO = 5.0


def contratos_vivos(hoy=None):
    """Los 8 contratos que HOY ocupan los huecos M1..M8, por calendario.

    Sale del calendario del CFE, no de lo que haya descargado: un contrato que
    no cotice deja su hueco vacio en vez de correr a los demas un puesto."""
    hoy = pd.Timestamp(hoy or dt.date.today())
    esc = dfv.escalera_vencimientos(hoy, hoy + pd.Timedelta(days=400))
    base = esc.searchsorted(hoy, side="right")
    out = []
    for k in range(dfv.N_MESES):
        v = esc[base + k]
        out.append(("M%d" % (k + 1),
                    "VX%s%d" % (dfv.CODIGOS[v.month - 1], v.year),
                    v))
    return out


def pedir_precios(tickers, verbose=True):
    """Los precios de ahora, EN UNA VENTANA. Secuencial y con reintento.

    Devuelve (dict ticker -> (precio, momento_vela), t_inicio, t_fin)."""
    from tvDatafeed import TvDatafeed, Interval
    tv = TvDatafeed()
    t_ini = dt.datetime.now()
    out = {}
    for tk in tickers:
        d = None
        for intento in range(REINTENTOS):
            try:
                d = tv.get_hist(symbol=tk, exchange="CBOE",
                                interval=Interval.in_5_minute, n_bars=5)
                if d is not None and len(d):
                    break
                d = None
            except Exception:
                d = None
            time.sleep(PAUSA_REINTENTO)
        if d is None:
            out[tk] = (None, None)
            if verbose:
                print("  %-9s SIN DATO tras %d intentos" % (tk, REINTENTOS))
        else:
            out[tk] = (float(d["close"].iloc[-1]), d.index[-1].to_pydatetime())
    return out, t_ini, dt.datetime.now()


def ultimo_cierre(serie):
    """La ultima fila COMPLETAMENTE cerrada de la serie de settlement.

    Es el respaldo de las patas rancias y, sobre todo, el corte de la vara: la
    historia contra la que se compara termina AQUI, nunca incluye hoy."""
    hoy = pd.Timestamp(dt.date.today())
    previos = serie[serie.index < hoy]
    if not len(previos):
        raise SystemExit("La serie no tiene ningun dia anterior a hoy.")
    return previos.index[-1], previos.iloc[-1]


def construir(verbose=True):
    """La foto completa: precios, antiguedades, percentiles y avisos."""
    serie = pcv.cargar_serie()
    f_cierre, fila_cierre = ultimo_cierre(serie)

    vivos = contratos_vivos()
    if verbose:
        print("Pidiendo %d contratos (secuencial, ventana unica)..." % len(vivos))
    precios, t_ini, t_fin = pedir_precios([tk for _, tk, _ in vivos], verbose)
    medio = t_ini + (t_fin - t_ini) / 2

    patas, curva = [], {}
    for hueco, tk, venc in vivos:
        px, vela = precios[tk]
        edad = None if vela is None else (medio - vela).total_seconds() / 60.0
        fresca = px is not None and edad is not None and edad <= MAX_EDAD_MIN
        if fresca:
            origen, valor = "vivo", px
        else:
            valor = fila_cierre.get(hueco)
            valor = None if (valor is None or np.isnan(valor)) else float(valor)
            origen = "cierre_previo" if valor is not None else "sin_dato"
        curva[hueco] = valor
        patas.append({"hueco": hueco, "contrato": tk,
                      "vencimiento": str(pd.Timestamp(venc).date()),
                      "precio": valor, "origen": origen,
                      "edad_min": None if edad is None else round(edad, 1),
                      "vela": None if vela is None else vela.strftime("%Y-%m-%d %H:%M")})

    # la vara: TODA la historia hasta el ultimo cierre, hoy excluido
    hist = serie[serie.index <= f_cierre]
    r_hist = pcv.ratios(hist)
    dte_hist = pcv.cargar_dte(r_hist.index)
    varas = pcv.varas_matriz(r_hist, dte=dte_hist)

    # el DTE de hoy: dias hasta el vencimiento del hueco M1
    venc_m1 = pd.Timestamp(vivos[0][2])
    dte_hoy = float((venc_m1 - pd.Timestamp(dt.date.today())).days)

    crudos = {}
    for i, j in pcv.PAREJAS:
        a, b = curva.get("M%d" % i), curva.get("M%d" % j)
        col = "M%d/M%d" % (j, i)
        crudos[col] = (b / a - 1.0) if (a and b) else None

    fiable = dict((p["hueco"], p["origen"] == "vivo") for p in patas)
    res = pcv.consultar_matriz(varas, crudos, dte_hoy)

    pares = {}
    for i, j in pcv.PAREJAS:
        col = "M%d/M%d" % (j, i)
        p, n = res.get(col, (None, 0))
        pares[col] = {"pct": None if p is None else round(p, 1),
                      "ratio": None if crudos[col] is None else round(crudos[col], 6),
                      "obs": int(n),
                      "fiable": bool(fiable.get("M%d" % i) and fiable.get("M%d" % j))}

    return {
        "momento": t_ini.strftime("%Y-%m-%d %H:%M:%S"),
        "ventana_s": round((t_fin - t_ini).total_seconds(), 2),
        "todas_a_la_vez": True,
        "patas_en_vivo": sum(1 for p in patas if p["origen"] == "vivo"),
        "patas_total": len(patas),
        "historia_hasta": str(pd.Timestamp(f_cierre).date()),
        "dte_m1": dte_hoy,
        "max_edad_min": MAX_EDAD_MIN,
        "patas": patas,
        "pares": pares,
    }


def pintar(v):
    """Resumen legible para consola y para Telegram."""
    L = []
    L.append("EN VIVO  %s   (ventana %.1f s, %d/%d patas en vivo)"
             % (v["momento"], v["ventana_s"], v["patas_en_vivo"], v["patas_total"]))
    L.append("Historia de referencia hasta %s   |   DTE del M1: %d dias"
             % (v["historia_hasta"], int(v["dte_m1"])))
    L.append("")
    for p in v["patas"]:
        if p["origen"] == "vivo":
            det = "%5.1f min" % p["edad_min"]
        elif p["origen"] == "cierre_previo":
            det = "CIERRE PREVIO"
        else:
            det = "SIN DATO"
        px = "   -   " if p["precio"] is None else "%7.3f" % p["precio"]
        L.append("  %-3s %-9s %s   %s" % (p["hueco"], p["contrato"], px, det))
    L.append("")
    pcts = dict((c, d["pct"]) for c, d in v["pares"].items() if d["pct"] is not None)
    L.append(pcv.pintar(pd.Series(pcts)))
    flojos = [c for c, d in v["pares"].items() if d["pct"] is not None and not d["fiable"]]
    if flojos:
        L.append("")
        L.append("%d parejas con alguna pata no viva (no disparan alerta)." % len(flojos))
    return "\n".join(L)


def extremos(v):
    """Parejas en extremo que SI son fiables (todas sus patas en vivo)."""
    alt, baj = [], []
    for c, d in v["pares"].items():
        if d["pct"] is None or not d["fiable"]:
            continue
        if d["pct"] >= UMBRAL_ALTO:
            alt.append((c, d["pct"]))
        elif d["pct"] <= UMBRAL_BAJO:
            baj.append((c, d["pct"]))
    return sorted(alt, key=lambda x: -x[1]), sorted(baj, key=lambda x: x[1])


def escribir_json(v):
    os.makedirs(SITIO, exist_ok=True)
    tmp = VIVO_JSON + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as fh:
        json.dump(v, fh, indent=1, ensure_ascii=True)
    os.replace(tmp, VIVO_JSON)
    return VIVO_JSON


def publicar(v):
    """Empuja solo vivo.json. Un commit pequeno cada cuarto de hora."""
    msg = "vivo %s (%d/%d patas)" % (v["momento"], v["patas_en_vivo"], v["patas_total"])
    for cmd in (["git", "add", "vivo.json"],
                ["git", "commit", "-m", msg],
                ["git", "push"]):
        p = subprocess.run(cmd, cwd=SITIO, capture_output=True, text=True)
        if p.returncode != 0:
            sal = (p.stdout + p.stderr).strip()
            if "nothing to commit" in sal:
                print("  (sin cambios que publicar)")
                return False
            print("  git fallo: %s" % sal[:200])
            return False
    print("  publicado")
    return True


def main():
    args = sys.argv[1:]
    v = construir()
    print("")
    print(pintar(v))
    print("")

    if "--dry-run" in args:
        print("--dry-run: no se escribe nada.")
        return

    print("Escrito: %s" % escribir_json(v))
    if "--push" in args:
        publicar(v)

    if "--enviar" in args:
        alt, baj = extremos(v)
        if not alt and not baj:
            print("Sin extremos fiables: no se envia nada.")
            return
        lin = ["<b>CURVA VIX - EN VIVO</b>  %s" % v["momento"], ""]
        for c, p in alt:
            lin.append("EXTREMO ALTO  %s en %d" % (c, round(p)))
        for c, p in baj:
            lin.append("EXTREMO BAJO  %s en %d" % (c, round(p)))
        lin += ["", "<pre>" + pcv.pintar(pd.Series(
            dict((c, d["pct"]) for c, d in v["pares"].items()
                 if d["pct"] is not None))) + "</pre>"]
        lin.append("Provisional: %d/%d patas en vivo, ventana %.1f s. "
                   "El dato firme (liquidacion oficial) entra manana a primera hora."
                   % (v["patas_en_vivo"], v["patas_total"], v["ventana_s"]))
        estado.avisar("\n".join(lin))
        print("Aviso enviado (%d altos, %d bajos)." % (len(alt), len(baj)))


if __name__ == "__main__":
    main()
