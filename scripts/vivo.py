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
  python vivo.py --enviar-siempre  # avisa por Telegram haya extremo o no (tarea de las 17:00)

SEMAFOROS (desde 2026-10-08): vivo.json lleva 'semaforos' (estado del ultimo cierre oficial +
lectura provisional del instante) y el Telegram de las 17:00 una linea por semaforo.
DESDE 2026-10-09 (orden del usuario: todo a las 17:00, nada a las 15:15) el Telegram de las 17:00 es
el UNICO aviso de entrada: si un semaforo certificado esta en VERDE con el cierre oficial, lleva que
hacer hoy y cuando salir (bloque_entrada). Sale aunque falle TradingView (foto_sin_vivo) y con 3
intentos de envio. La tarea "VIX CURVE Semaforos 15h15" queda DESHABILITADA.
  python vivo.py --push --avisos-vivo   # (tarea Vivo, cada 15 min) cuenta lecturas seguidas en verde
                 y manda el AVISO EN VIVO como mucho una vez al dia por semaforo

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
import semaforos
import descargar_futuros_vix as dfv
import aviso_semaforos as avs

HERE = os.path.dirname(os.path.abspath(__file__))
SITIO = os.path.join(HERE, "sitio")
VIVO_JSON = os.path.join(SITIO, "vivo.json")

MAX_EDAD_MIN = 120     # mas viejo que esto -> pata rancia, se usa el cierre previo
REINTENTOS = 3
PAUSA_REINTENTO = 0.8
UMBRAL_ALTO = 95.0     # los mismos que el radar nocturno
UMBRAL_BAJO = 5.0
TICKER_SPOT = "VIX"    # indice al contado en TradingView (CBOE:VIX)
URL_WEB = "https://manumartinb.github.io/vix-term-structure/"


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
    # el VIX al contado va en la MISMA ventana que los 8 futuros (secuencial)
    precios, t_ini, t_fin = pedir_precios([tk for _, tk, _ in vivos] + [TICKER_SPOT], verbose)
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

    sems = construir_semaforos(hist, curva, fiable)

    base = construir_base(serie, f_cierre, r_hist.index, dte_hist, patas, vivos,
                          precios[TICKER_SPOT], medio, dte_hoy)

    return {
        "base": base,
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
        "semaforos": sems,
    }


def construir_base(serie, f_cierre, idx_hist, dte_hist, patas, vivos, spot_tv, medio, dte_hoy):
    """BASE_CM30 de AHORA (futuro a 30d / VIX spot - 1) y su percentil.

    Misma regla que la web: pcv.base_cm30 + percentil condicional por DTE del M1,
    invertido. La vara es la serie historica CERRADA (spot = cierre oficial del
    Cboe, cache de la web); el dia de hoy no entra. Solo es 'fiable' (puede
    disparar extremo) si el spot y las patas M1 y M2 estan EN VIVO."""
    px_spot, vela = spot_tv
    edad = None if vela is None else (medio - vela).total_seconds() / 60.0
    spot_vivo = px_spot is not None and edad is not None and edad <= MAX_EDAD_MIN

    ruta = os.path.join(SITIO, "data", "vix_spot_cboe.csv")
    spot_h = pd.read_csv(ruta, parse_dates=["Fecha"]).set_index("Fecha")["VIX"]
    spot_h = spot_h.reindex(idx_hist)          # sin relleno: un dia sin cierre queda sin base
    det = pd.read_csv(pcv.DETALLE, parse_dates=["Fecha", "VENC_M1", "VENC_M2"])
    det = det.set_index("Fecha").reindex(idx_hist)
    d1 = (det["VENC_M1"] - det.index).dt.days
    d2 = (det["VENC_M2"] - det.index).dt.days
    hist = pcv.base_cm30(serie["M1"].reindex(idx_hist), serie["M2"].reindex(idx_hist),
                         d1, d2, spot_h)
    listas = pcv.vara(hist.values, dte_hist)

    m = dict((p["hueco"], p) for p in patas)
    m1, m2 = m["M1"]["precio"], m["M2"]["precio"]
    out = {"spot": px_spot if spot_vivo else None, "spot_edad_min": None if edad is None else round(edad, 1),
           "spot_vivo": bool(spot_vivo), "valor": None, "pct": None, "obs": 0, "fiable": False}
    if not spot_vivo:
        # sin spot en vivo no hay base de ahora (no se mezcla con el cierre de ayer)
        return out
    if m1 is None or m2 is None:
        return out
    dte2 = float((pd.Timestamp(vivos[1][2]) - pd.Timestamp(dt.date.today())).days)
    val = float(pcv.base_cm30(m1, m2, dte_hoy, dte2, px_spot))
    pct, n = pcv.consultar(listas, val, dte_hoy)
    out.update({"valor": round(val, 6), "pct": None if pct is None else round(pct, 1),
                "obs": int(n),
                "fiable": bool(m["M1"]["origen"] == "vivo" and m["M2"]["origen"] == "vivo")})
    return out


def construir_semaforos(hist, curva, fiable):
    """Oficial (ultimo cierre) + en vivo (provisional) de cada semaforo. Nunca tumba vivo.py."""
    try:
        ofi = dict((s["id"], s) for s in semaforos.evaluar_todos(hist))
        reg = dict((s["id"], s) for s in semaforos.SEMAFOROS)
        out = []
        for x in semaforos.evaluar_vivo_todos(curva, fiable):
            o = ofi.get(x["id"], {})
            x.update({"nombre": o.get("nombre"), "variable": o.get("variable"), "apuesta": o.get("apuesta"),
                      "certificada": bool(reg.get(x["id"], {}).get("certificada", True)), "edge": reg.get(x["id"], {}).get("edge"),
                      "formato": o.get("formato"), "oficial_estado": o.get("estado"),
                      "oficial_fecha": o.get("fecha_cierre"), "oficial_valor": o.get("valor"),
                      # para el Telegram de las 17:00 (que hacer si el oficial esta en verde)
                      "oficial_racha": o.get("racha"), "oficial_motivo": o.get("motivo"),
                      "accion_si": o.get("accion_si"), "salida": o.get("salida")})
            out.append(x)
        return out
    except Exception as e:
        print("  (semaforos sin calcular: %s)" % str(e)[:150])
        return []


def lineas_semaforos(v, html=False):
    """Una linea por semaforo: oficial (manda) + en vivo (provisional). Sin '<' ni '>' en el texto."""
    L = []
    for s in v.get("semaforos", []):
        if html and not s.get("certificada", True):
            continue                      # informativa (no certificada): fuera de los Telegram
        fmt = s.get("formato") or "num"
        ofi = s.get("oficial_estado")
        verde = ofi == "SI"
        etq = "VERDE" if verde else ("no" if ofi == "NO" else "sin dato")
        ini = ("\U0001F7E2 " if verde else "\u26AA ") if html else ""
        e = semaforos.esc if html else (lambda x: x)
        if not s.get("certificada", True):
            etq += " (informativa, no certificada)"
        if html and s.get("edge"):
            etq += " (edge %s)" % e(s["edge"])
        nom = ("<b>%s: %s</b>" % (e(s.get("nombre")), etq)) if (html and verde) else ("%s: %s" % (e(s.get("nombre")), etq))
        t = "%s%s (cierre %s: %s %s)" % (ini, nom, semaforos.fecha_corta(s.get("oficial_fecha")),
                                       e(s.get("variable")), semaforos.txt_valor(fmt, s.get("oficial_valor")))
        if s.get("valor") is not None:
            t += ". En vivo %s%s" % (semaforos.txt_valor(fmt, s["valor"]), "" if s.get("fiable") else " (no fiable)")
            if s.get("estado") != ofi:
                t += ", hoy cerraria %s" % ("VERDE" if s.get("estado") == "SI" else "en no")
        L.append(t + ".")
    return L


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
    L.append("")
    L.append(linea_base(v["base"]))
    sl = lineas_semaforos(v)
    if sl:
        L.append("")
        L.extend(["SEMAFORO " + x for x in sl])
    return "\n".join(L)


def linea_base(b):
    """Una linea con la base futuro-30d / VIX spot, para consola y Telegram."""
    if b["valor"] is None:
        return ("Base (futuro 30d vs VIX spot): sin dato de ahora "
                "(spot en vivo: %s)." % ("si" if b["spot_vivo"] else "NO"))
    pct = "sin percentil (tramo con pocas observaciones)" if b["pct"] is None \
        else "percentil %d" % round(b["pct"])
    return ("Base (futuro 30d vs VIX spot %.2f): %+.2f%%, %s%s"
            % (b["spot"], 100.0 * b["valor"], pct, "" if b["fiable"] else "  *no fiable"))


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
    b = v["base"]
    if b["fiable"] and b["pct"] is not None:
        if b["pct"] >= UMBRAL_ALTO:
            alt.append(("BASE_CM30", b["pct"]))
        elif b["pct"] <= UMBRAL_BAJO:
            baj.append(("BASE_CM30", b["pct"]))
    return sorted(alt, key=lambda x: -x[1]), sorted(baj, key=lambda x: x[1])


def bloque_entrada(sems, hoy=None):
    """Lo que hay que HACER hoy segun el cierre oficial (desde 9-oct-2026 va en el Telegram de las
    17:00; el aviso aparte de las 15:15 se retiro por orden del usuario). Solo semaforos certificados.
    Verde -> que comprar/vender y cuando salir; SIN DATO -> no se puede evaluar; cierre atrasado ->
    se dice. Dia sin sesion del CFE -> nada (no hay entrada)."""
    hoy = pd.Timestamp(hoy or dt.date.today()).normalize()
    if not avs.es_sesion(hoy):
        return []
    e = semaforos.esc
    cert = [s for s in sems if s.get("certificada", True)]
    verdes = [s for s in cert if s.get("oficial_estado") == "SI"]
    L = []
    if len(verdes) >= 2:
        L.append("<b>LUZ VERDE DOBLE: %s</b>" % e(" + ".join(s.get("nombre") for s in verdes)))
        ap = set(s.get("apuesta") for s in verdes)
        if len(ap) == 1 and None not in ap:
            L.append("Ojo: es la misma apuesta (%s); se mueven casi a la par. Operar las dos suma riesgo." % e(ap.pop()))
    if verdes:
        L.append("Hora: la regla entra a las 10:30 de Nueva York (%s en Espana); este aviso sale despues, "
                 "asi que se entra al recibirlo. El verde ya se ve en la web desde las 08:00." % semaforos.hora_espana(hoy))
    for s in verdes:
        r = s.get("oficial_racha")
        L.append("\U0001F7E2 <b>ENTRADA HOY: %s</b>%s%s" % (
            e(s.get("nombre")), (" (edge %s)" % e(s["edge"])) if s.get("edge") else "",
            "" if not r else (", 1 sesion en verde" if r == 1 else ", %d sesiones en verde" % r)))
        L.append("Entrada: " + e(s.get("accion_si")))
        L.append("Salida: " + e(s.get("salida")))
    for s in cert:
        if s.get("oficial_estado") == "SIN DATO":
            L.append("\u26A0\uFE0F <b>%s: SIN DATO</b>. %s Hoy no se puede evaluar la regla."
                     % (e(s.get("nombre")), e(s.get("oficial_motivo") or "")))
    esperado = avs.sesion_anterior(hoy)
    if any(s.get("oficial_fecha") and pd.Timestamp(s["oficial_fecha"]).normalize() < esperado for s in cert):
        L.append("OJO: el ultimo cierre oficial no es el de la sesion anterior (%s): el dato puede estar atrasado."
                 % esperado.strftime("%d/%m"))
    return L


def mensaje_telegram(v, alt, baj):
    """El mensaje de las 17:00, corto: titular, semaforos (y la entrada de hoy si alguno esta en verde
    con el cierre oficial), triangulo, base y enlace.

    OJO: va en parse_mode HTML. NADA de '<' ni '>' sueltos en el texto (un '<=' lo
    toma por etiqueta y Telegram devuelve 400: paso el 5-oct 17:00)."""
    hora = v["momento"][11:16]
    if v["patas_en_vivo"] == 0:
        # festivo USA o mercado cerrado: mejor decirlo que mandar la curva de ayer. Los semaforos
        # oficiales SI van (salen del cierre, no de la lectura en vivo): un fallo de TradingView no
        # puede tapar una entrada.
        lin = ["<b>CURVA VIX %s</b>: sin datos en vivo%s." % (
            hora, "" if avs.es_sesion(pd.Timestamp(v["momento"][:10])) else " (mercado cerrado o festivo)")]
        if avs.es_sesion(pd.Timestamp(v["momento"][:10])):
            lin += lineas_semaforos(dict(v, semaforos=[dict(s, valor=None) for s in v.get("semaforos", [])]), html=True)
            lin += bloque_entrada(v.get("semaforos", []), v["momento"][:10])
            lin.append('<a href="%s">Abrir panel</a>' % URL_WEB)
        return "\n".join(lin)
    if alt or baj:
        tit = ", ".join(["%s ALTO %d" % (c, round(p)) for c, p in alt] +
                        ["%s BAJO %d" % (c, round(p)) for c, p in baj])
        tit = "EXTREMO: " + tit
    else:
        tit = "sin extremos"
    lin = ["<b>CURVA VIX %s</b> | %s (%d/%d patas en vivo)"
           % (hora, tit, v["patas_en_vivo"], v["patas_total"])]
    lin += lineas_semaforos(v, html=True)
    lin += bloque_entrada(v.get("semaforos", []), v["momento"][:10])
    lin += [
           "<pre>" + pcv.pintar(pd.Series(
               dict((c, d["pct"]) for c, d in v["pares"].items()
                    if d["pct"] is not None))) + "</pre>"]
    b = v["base"]
    if b["valor"] is None:
        lin.append("Base: sin VIX spot en vivo")
    else:
        lin.append("Base 30d vs spot %.2f: %+.1f%%, percentil %s%s"
                   % (b["spot"], 100.0 * b["valor"],
                      "-" if b["pct"] is None else "%d" % round(b["pct"]),
                      "" if b["fiable"] else " (no fiable)"))
    muertas = [p["hueco"] for p in v["patas"] if p["origen"] != "vivo"]
    if muertas:
        lin.append("Sin dato vivo: %s (no cuentan para alertas)" % ", ".join(muertas))
    lin.append("100 = backwardation, 0 = contango. Provisional.")
    lin.append('<a href="%s">Abrir panel</a>' % URL_WEB)
    return "\n".join(lin)


ESTADO_VIVO = os.path.join(HERE, "data", "semaforos_vivo_estado.json")


def leer_estado_vivo(hoy, path=None):
    """Estado del dia de los avisos en vivo. Cambia de fecha -> se empieza de cero."""
    path = path or ESTADO_VIVO
    try:
        with io.open(path, encoding="utf-8") as fh:
            e = json.load(fh)
    except Exception:
        e = {}
    if e.get("fecha") != hoy:
        e = {"fecha": hoy, "sem": {}}
    e.setdefault("sem", {})
    return e


def guardar_estado_vivo(e, path=None):
    path = path or ESTADO_VIVO
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as fh:
        json.dump(e, fh, indent=1)
    os.replace(tmp, path)


def contar_lecturas(v, e):
    """Cuenta lecturas SEGUIDAS en verde (solo las fiables: patas del momento) y anota en
    v["semaforos"] si el verde en vivo esta confirmado. Una lectura no fiable ni suma ni corta."""
    defs = dict((s["id"], s) for s in semaforos.SEMAFOROS)
    hora = v["momento"][11:16]
    for s in v.get("semaforos", []):
        d = defs.get(s["id"], {})
        st = e["sem"].setdefault(s["id"], {"lecturas_si": 0, "avisado": False, "historial": []})
        if s.get("fiable") and s.get("valor") is not None:
            st["lecturas_si"] = st["lecturas_si"] + 1 if s.get("estado") == "SI" else 0
            st["historial"] = (st.get("historial", []) + [[hora, s.get("valor"), s.get("estado")]])[-40:]
        n_conf = int(d.get("confirmaciones", 2))
        s["lecturas_si"] = st["lecturas_si"]
        s["confirmaciones"] = n_conf
        s["confirmado"] = bool(st["lecturas_si"] >= n_conf and s.get("fiable") and s.get("estado") == "SI")
        s["aviso_vivo"] = d.get("aviso_vivo")
        s["avisado_hoy"] = bool(st.get("avisado"))
    return e


def mensaje_aviso_vivo(s, d, hora, prueba=False):
    """Telegram del verde en vivo. Sin '<' ni '>' sueltos (parse_mode HTML)."""
    fmt = s.get("formato") or "num"
    L = []
    if prueba:
        L.append("<b>PRUEBA del aviso en vivo (no es senal)</b>")
    esc = semaforos.esc
    tx = semaforos.textos(d, semaforos.siguiente_sesion(dt.date.today()))   # {hora_es} de la sesion siguiente
    if d.get("aviso_vivo") == "entrada":
        L.append("\U0001F7E2 <b>ENTRADA EN VIVO: %s</b>%s" % (esc(s.get("nombre")), (" (edge %s)" % esc(d["edge"])) if d.get("edge") else ""))
    else:
        L.append("\U0001F7E1 <b>AVISO PREVIO: %s</b>%s" % (esc(s.get("nombre")), (" (edge %s)" % esc(d["edge"])) if d.get("edge") else ""))
    L.append("En vivo %s = %s a las %s (confirmado en %d lecturas seguidas)."
             % (esc(s.get("variable")), semaforos.txt_valor(fmt, s.get("valor")), hora, s.get("lecturas_si", 0)))
    L.append(esc(tx["accion_si"] if d.get("aviso_vivo") == "entrada" else tx["aviso_previo_txt"]))
    L.append('<a href="%s">Abrir panel</a>' % URL_WEB)
    return "\n".join(x for x in L if x)


def avisos_vivo(v, e, enviar=None):
    """Como mucho UN aviso al dia por semaforo: verde en vivo confirmado, lectura actual fiable y
    semaforo oficial todavia en NO (si ya esta verde con el cierre, lo cubre el Telegram de las 17:00).
    Si Telegram falla no se marca como enviado: se reintenta en la lectura siguiente."""
    enviar = enviar or estado.avisar
    defs = dict((s["id"], s) for s in semaforos.SEMAFOROS)
    hora = v["momento"][11:16]
    pend = []
    for s in v.get("semaforos", []):
        d = defs.get(s["id"])
        st = e["sem"].get(s["id"])
        if not d or not st or not d.get("aviso_vivo") or not d.get("certificada", True):
            continue
        if st.get("avisado") or not s.get("confirmado") or s.get("oficial_estado") == "SI":
            continue
        pend.append((s, d, st))
    if not pend:
        return e
    # UN solo Telegram por lectura aunque se confirmen varios semaforos a la vez (LONG PUT + SHORT CALL)
    msgs = [mensaje_aviso_vivo(s, d, hora) for (s, d, st) in pend]
    if len(msgs) > 1:
        cab = "<b>AVISO EN VIVO DOBLE: %s</b>" % semaforos.esc(" + ".join(s.get("nombre") for (s, d, st) in pend))
        ap = set(d.get("apuesta") for (s, d, st) in pend)
        if len(ap) == 1 and None not in ap:
            cab += "\nOjo: misma apuesta (%s); operar las dos suma riesgo." % semaforos.esc(ap.pop())
        txt = cab + "\n\n" + "\n\n".join(m.rsplit("\n", 1)[0] for m in msgs[:-1]) + "\n\n" + msgs[-1]
    else:
        txt = msgs[0]
    nombres = ", ".join(s.get("nombre") for (s, d, st) in pend)
    if enviar(txt):
        for (s, d, st) in pend:
            st["avisado"] = True
            st["hora_aviso"] = hora
        print("AVISO EN VIVO enviado: %s (%s)" % (nombres, hora))
    else:
        print("FALLO del aviso en vivo de %s: se reintenta en la lectura siguiente." % nombres)
    return e


def escribir_json(v):
    os.makedirs(SITIO, exist_ok=True)
    # tmp UNICO por proceso: a las 17:00 la tarea Vivo y la del Aviso 17h escriben a la vez y con un
    # tmp compartido el que perdia se caia (auditoria 2026-10-08, B-H3, reproducido en laboratorio)
    tmp = "%s.%d.tmp" % (VIVO_JSON, os.getpid())
    with io.open(tmp, "w", encoding="utf-8") as fh:
        json.dump(v, fh, indent=1, ensure_ascii=True)
    for k in range(10):
        try:
            os.replace(tmp, VIVO_JSON)
            break
        except PermissionError:
            if k == 9:
                raise
            time.sleep(0.3 * (k + 1))
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


def foto_sin_vivo(error):
    """Si la lectura en vivo revienta, el Telegram de las 17:00 sale igual con los semaforos del cierre
    oficial (desde 9-oct-2026 es el UNICO aviso de entrada: no puede depender de TradingView)."""
    serie = pcv.cargar_serie()
    hist = serie[serie.index < pd.Timestamp(dt.date.today())]
    sems = construir_semaforos(hist, {}, {})
    print("  lectura en vivo FALLIDA (%s): se avisa solo con el cierre oficial" % str(error)[:200])
    return {"momento": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "patas_en_vivo": 0,
            "patas_total": dfv.N_MESES, "semaforos": sems, "error_vivo": str(error)[:200]}


def main():
    args = sys.argv[1:]
    siempre = "--enviar-siempre" in args
    try:
        v = construir()
    except Exception as exc:
        if not siempre:
            raise
        v = foto_sin_vivo(exc)
        txt = mensaje_telegram(v, [], [])
        if "--dry-run" in args:
            print("--dry-run, mensaje:\n" + txt.encode("ascii", "replace").decode("ascii"))
            return
        k = avs.enviar_con_reintentos(txt)
        print("Aviso (solo cierre oficial) %s." % ("enviado al intento %d" % k if k else "NO enviado"))
        sys.exit(1)                          # el fallo del vivo tiene que verse en el Programador
    print("")
    print(pintar(v))
    print("")

    avisos = "--avisos-vivo" in args
    e = None
    if avisos:
        e = leer_estado_vivo(v["momento"][:10])
        contar_lecturas(v, e)
        for s in v.get("semaforos", []):
            print("VIVO %s: %s, %d lectura(s) seguidas en verde de %d, confirmado %s, avisado hoy %s"
                  % (s.get("nombre"), s.get("estado"), s.get("lecturas_si", 0), s.get("confirmaciones", 0),
                     "SI" if s.get("confirmado") else "no", "SI" if s.get("avisado_hoy") else "no"))

    if "--dry-run" in args:
        print("--dry-run: no se escribe nada.")
        if siempre:
            alt, baj = extremos(v)
            print("--dry-run, mensaje de las 17:00:\n"
                  + mensaje_telegram(v, alt, baj).encode("ascii", "replace").decode("ascii"))
        return

    print("Escrito: %s" % escribir_json(v))
    if "--push" in args:
        publicar(v)
    if avisos:
        if v["patas_en_vivo"] > 0:
            avisos_vivo(v, e)
        guardar_estado_vivo(e)

    if "--enviar" in args or "--enviar-siempre" in args:
        alt, baj = extremos(v)
        if not alt and not baj and "--enviar-siempre" not in args:
            print("Sin extremos fiables: no se envia nada.")
            return
        # 3 intentos (0, 20, 60 s): desde 9-oct-2026 este mensaje lleva la ENTRADA del dia
        k = avs.enviar_con_reintentos(mensaje_telegram(v, alt, baj))
        if k:
            print("Aviso enviado al intento %d (%d altos, %d bajos)." % (k, len(alt), len(baj)))
        else:
            # antes imprimia "enviado" y salia con 0 aunque Telegram dijera 400
            # (5-oct 17:00): el fallo quedaba invisible para el Programador de tareas.
            print("FALLO: el aviso NO se envio.")
            sys.exit(1)


if __name__ == "__main__":
    main()
