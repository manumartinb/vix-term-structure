"""
construir_sitio.py -- genera el sitio estatico de la curva de futuros VIX.

DISENO: el mismo de los demas dashboards del usuario en GitHub Pages
(BURST_RADAR_CALENDAR, COCKPIT_BATMAN_LT): tema oscuro GitHub
(#0d1117 / #161b22 / #c9d1d9 / #30363d), Plotly 2.30.0 desde CDN, cabecera con
KPIs y bloques .note. Los 28 paneles van A TODO EL ANCHO y apilados, cada uno
con su selector de rango (90D / 1A / 3A / 10A / All) y el zoom de Plotly.

SALIDA (carpeta sitio/ = raiz del repo manumartinb/vix-term-structure):
  index.html                      panel con los 28 pares
  data.json                       series que consume la pagina
  data/vix_futuros_M1_M8.csv      serie completa (descarga)
  data/vix_percentiles_pares.csv  percentil expanding por par (descarga)

QUE PINTA
Un panel por par Mj/Mi (28). Percentil EXPANDING: para cada dia, donde queda el
ratio de ese dia dentro de TODA su historia previa (sin mirar al futuro).
Convencion heredada del VIX Studio:
  100 = backwardation extrema (curva del reves)   0 = contango extremo

Un dia no se publica hasta que su tramo de DTE junta MIN_OBS_TRAMO observaciones
comparables (el umbral vive en percentiles_curva.py).

RENDIMIENTO: 28 graficos Plotly de ~4.900 puntos no se pintan de golpe. Se
dibujan bajo demanda con IntersectionObserver segun entran en pantalla.

Reglas tecnicas del proyecto: ASCII, cp1252.
"""

import os
import json
import bisect
import datetime as dt

import numpy as np
import pandas as pd

import percentiles_curva as pcv

HERE = os.path.dirname(os.path.abspath(__file__))
SITIO = os.path.join(HERE, "sitio")
SITIO_DATA = os.path.join(SITIO, "data")

UMB_ALTO, UMB_BAJO = 95.0, 5.0

# Los parametros del percentil condicional (ventana, cap, minimo) viven en
# percentiles_curva.py: es la UNICA fuente del calculo.

VERDE, AMBAR, ROJO, AZUL = "#3fb950", "#d29922", "#f85149", "#58a6ff"


def color_pct(v):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "#8b949e"
    if v >= UMB_ALTO:
        return ROJO
    if v <= UMB_BAJO:
        return AZUL
    if v >= 80 or v <= 20:
        return AMBAR
    return "#c9d1d9"


def racha_zona(serie):
    """Dias seguidos (hasta hoy) en la misma zona: alta (>=80), baja (<=20), media."""
    s = serie[~np.isnan(serie)]
    if len(s) == 0:
        return 0, "media"

    def zona(v):
        return "alta" if v >= 80 else ("baja" if v <= 20 else "media")

    z = zona(s[-1])
    n = 0
    for v in s[::-1]:
        if zona(v) == z:
            n += 1
        else:
            break
    return n, z


def cargar_spx(fechas):
    """Cierre del SPX (^GSPC) alineado a las fechas de la serie, como referencia de
    fondo en cada grafico. Se cachea en data/spx_close.csv para no re-descargar."""
    cache = os.path.join(SITIO_DATA, "spx_close.csv")
    hoy = dt.date.today().isoformat()
    spx = None
    if os.path.exists(cache):
        try:
            c = pd.read_csv(cache, parse_dates=["Fecha"]).set_index("Fecha")["SPX"]
            if len(c) and c.index.max().date().isoformat() >= str(fechas[-1].date()):
                spx = c
                print("  SPX desde cache (%d dias)" % len(c))
        except Exception:
            spx = None
    if spx is None:
        try:
            import yfinance as yf
            print("  descargando ^GSPC...")
            d = yf.download("^GSPC", start="2007-01-01", progress=False, auto_adjust=True)
            cl = d["Close"]
            if hasattr(cl, "columns"):
                cl = cl.iloc[:, 0]
            cl.index = pd.to_datetime(cl.index).tz_localize(None).normalize()
            spx = cl.dropna()
            spx.name = "SPX"
            os.makedirs(SITIO_DATA, exist_ok=True)
            spx.rename_axis("Fecha").to_frame("SPX").to_csv(cache)
            print("  SPX descargado (%d dias, hasta %s)" % (len(spx), spx.index.max().date()))
        except Exception as e:
            print("  AVISO: no se pudo obtener el SPX (%s). Los graficos iran sin fondo."
                  % str(e)[:70])
            return None
    # alineado a las fechas del panel; ffill para festivos propios de cada mercado
    ali = spx.reindex(pd.DatetimeIndex(fechas)).ffill()
    return [None if pd.isna(v) else round(float(v), 2) for v in ali.values]


# ---------------------------------------------------------------------------
# Metricas que el VIX Studio tenia y faltaban aqui (recuperadas 2026-09-21)
# ---------------------------------------------------------------------------
TRIPLES = [(i, i + 1, i + 2) for i in range(1, 7)]   # 6 mariposas: 123, 234 ... 678


def series_convexidad(serie):
    """Curvatura de cada trio consecutivo: P(n) - 2*P(n+1) + P(n+2).

    Es la 'panza' de la curva. Replica Live!B25:G25 del VIX Studio
    (`K3-2*L3+M3` y sucesivos). OJO A LA CONVENCION: en su hoja la convexidad
    NO va invertida (a diferencia de la pendiente), asi que aqui tampoco:
    percentil ALTO = concavo (decay a favor del front), BAJO = convexo."""
    out = {}
    for a, b, c in TRIPLES:
        out["C%d%d%d" % (a, b, c)] = (serie["M%d" % a] - 2 * serie["M%d" % b]
                                      + serie["M%d" % c])
    return pd.DataFrame(out, index=serie.index)


def cargar_vix_spot(fechas):
    """Cierre del indice VIX al contado (^VIX), cacheado."""
    cache = os.path.join(SITIO_DATA, "vix_spot.csv")
    spot = None
    if os.path.exists(cache):
        try:
            c = pd.read_csv(cache, parse_dates=["Fecha"]).set_index("Fecha")["VIX"]
            if len(c) and c.index.max() >= fechas[-1]:
                spot = c
                print("  VIX spot desde cache (%d dias)" % len(c))
        except Exception:
            spot = None
    if spot is None:
        try:
            import yfinance as yf
            print("  descargando ^VIX...")
            d = yf.download("^VIX", start="2007-01-01", progress=False, auto_adjust=True)
            cl = d["Close"]
            if hasattr(cl, "columns"):
                cl = cl.iloc[:, 0]
            cl.index = pd.to_datetime(cl.index).tz_localize(None).normalize()
            spot = cl.dropna()
            spot.name = "VIX"
            spot.rename_axis("Fecha").to_frame("VIX").to_csv(cache)
            print("  VIX spot descargado (%d dias)" % len(spot))
        except Exception as e:
            print("  AVISO: sin VIX al contado (%s)" % str(e)[:60])
            return None
    return spot.reindex(pd.DatetimeIndex(fechas)).ffill()


def series_base(serie, detalle):
    """M1 frente al VIX al contado. Dos lecturas:

      BASE_PCT   = M1/spot - 1          (la base clasica, en tanto por uno)
      BASE_DIA   = (M1 - spot) / DTE    (prima por dia de vida que queda)

    La segunda es la que tenia el VIX Studio en Live!H1 (`(K3-B1)/D1`), que era
    precisamente la celda que alimentaba la alerta U4 -- la que estaba muerta
    porque U4 contenia el texto "(disabled)" en vez de una formula."""
    spot = cargar_vix_spot(serie.index)
    if spot is None:
        return pd.DataFrame(index=serie.index)
    dte = (pd.to_datetime(detalle["VENC_M1"]) - detalle["Fecha"]).dt.days
    dte.index = pd.DatetimeIndex(detalle["Fecha"])
    dte = dte.reindex(serie.index)
    # SOLO vencimiento constante a 30 dias. Se retiraron BASE_PCT (M1/spot-1) y
    # BASE_DIA ((M1-spot)/DTE) el 2026-09-21 por REDUNDANTES: medidas entre si daban
    # r de Pearson 0,85-0,94 y coincidian de zona el 78-87% de los dias, o sea que
    # eran la misma senal con tres acentos. El desempate fue el comportamiento en el
    # roll (salto medio de un dia a otro):
    #     BASE_PCT   18,9 con DTE<=2  vs 12,3 con DTE>5   -> 1,54x mas nerviosa
    #     BASE_DIA   19,3             vs 12,0             -> 1,61x
    #     BASE_CM30   8,5             vs 10,1             -> 0,84x (se CALMA)
    # CM30 es la unica inmune, porque su madurez no cambia nunca. Ademas es el
    # estandar del sector y la misma ponderacion que ya hacia la celda Live!H25.
    dte2 = (pd.to_datetime(detalle["VENC_M2"]) - detalle["Fecha"]).dt.days
    dte2.index = pd.DatetimeIndex(detalle["Fecha"])
    dte2 = dte2.reindex(serie.index)
    w = ((dte2 - 30) / (dte2 - dte)).clip(0, 1)
    out = pd.DataFrame(index=serie.index)
    out["BASE_CM30"] = (w * serie["M1"] + (1 - w) * serie["M2"]) / spot - 1.0
    return out


def envolvente(serie):
    """Forma de la curva de HOY contra su abanico historico.

    Se normaliza cada dia dividiendo por su propio M1, para quitar el nivel y
    dejar solo la FORMA (si no, el abanico lo dominaria que el VIX estuviera en
    12 o en 80). Devuelve los percentiles 5/25/50/75/95 de cada vencimiento."""
    norm = serie.div(serie["M1"], axis=0)
    norm = norm[serie["M1"] > 0]
    q = {}
    for p in (5, 25, 50, 75, 95):
        q["p%02d" % p] = [None if pd.isna(v) else round(float(v), 4)
                          for v in norm.quantile(p / 100.0).values]
    return q


def mapa_calor(pct, semanas=True):
    """Historia entera de los 28 pares como rejilla par x tiempo.

    Se agrega a semanal (media) para que la rejilla sea manejable: 4.900 dias x
    28 pares son 137.000 celdas y el navegador se arrastra. A escala semanal los
    manchones de 2008, 2018 y 2020 se ven igual de bien."""
    g = pct.resample("W").mean() if semanas else pct
    g = g.dropna(how="all")
    z = []
    for col in pct.columns:
        z.append([None if pd.isna(v) else int(round(v)) for v in g[col].values])
    return {"fechas": [d.strftime("%Y-%m-%d") for d in g.index],
            "pares": list(pct.columns), "z": z}


def main():
    os.makedirs(SITIO_DATA, exist_ok=True)
    serie = pcv.cargar_serie()
    r = pcv.ratios(serie)

    # dias a vencimiento del front month: es la variable del ciclo para TODAS las
    # familias (todos los contratos ruedan el mismo dia)
    detalle = pd.read_csv(os.path.join(HERE, "data", "vix_futuros_M1_M8_detalle.csv"),
                          parse_dates=["Fecha", "VENC_M1", "VENC_M2"])
    dte_s = (detalle["VENC_M1"] - detalle["Fecha"]).dt.days
    dte_s.index = pd.DatetimeIndex(detalle["Fecha"])
    dte_s = dte_s.reindex(r.index)
    dte_v = dte_s.values.astype(float)

    print("Percentil CONDICIONAL por DTE (+/-%d, cap %d) de %d pares sobre %d sesiones..."
          % (pcv.VENTANA_DTE, pcv.CAP_DTE, len(r.columns), len(r)))
    pct = pcv.matriz_percentiles(r, dte_v)

    # --- familias que faltaban (recuperadas del VIX Studio) ---
    conv_raw = series_convexidad(serie)
    base_raw = series_base(serie, detalle)
    print("Convexidad (%d series) y base M1-spot (%d series)..."
          % (conv_raw.shape[1], base_raw.shape[1]))
    conv = pd.DataFrame(index=serie.index)
    for c in conv_raw.columns:
        # SIN invertir: en el VIX Studio la convexidad no lleva el 1-p
        conv[c] = pcv.percentil_condicional(conv_raw[c].values, dte_v, invertir=False)
    base = pd.DataFrame(index=serie.index)
    for c in base_raw.columns:
        base[c] = pcv.percentil_condicional(base_raw[c].values, dte_v)   # invertida, como U4

    completos = pct.dropna()
    ultimo = completos.index[-1] if len(completos) else pct.dropna(how="all").index[-1]
    hoy = pct.loc[ultimo]
    curva = serie.loc[ultimo].dropna()

    # ---- ficheros de descarga
    serie.reset_index().to_csv(os.path.join(SITIO_DATA, "vix_futuros_M1_M8.csv"), index=False)
    pct.round(2).reset_index().to_csv(
        os.path.join(SITIO_DATA, "vix_percentiles_pares.csv"), index=False)

    # ---- data.json que consume la pagina
    fechas = [d.strftime("%Y-%m-%d") for d in pct.index]
    pares = {}
    for col in pct.columns:
        pares[col] = [None if np.isnan(v) else round(float(v), 1) for v in pct[col].values]

    meta = []
    for i, j in pcv.PAREJAS:
        col = "M%d/M%d" % (j, i)
        v = hoy.get(col, np.nan)
        s = pct[col].values
        val = pct[col].dropna()
        n, z = racha_zona(s)
        meta.append({
            "par": col, "i": i, "j": j,
            "hoy": None if np.isnan(v) else round(float(v), 1),
            "color": color_pct(v),
            "mediana": round(float(val.median()), 1) if len(val) else None,
            "racha": n, "zona": z,
            "desde": val.index[0].strftime("%Y-%m") if len(val) else None,
        })

    meta_extra = []
    for fam, df, etiqueta in (("conv", conv, "Convexidad"), ("base", base, "Base M1-spot")):
        for c in df.columns:
            v = df[c].iloc[-1] if len(df) else np.nan
            vv = df[c].dropna()
            # el ultimo valor no-nulo (el dia en curso puede no tener M8 ni base)
            v = vv.iloc[-1] if len(vv) else np.nan
            n, z = racha_zona(df[c].values)
            meta_extra.append({
                "fam": fam, "par": c,
                "hoy": None if np.isnan(v) else round(float(v), 1),
                "color": color_pct(v),
                "mediana": round(float(vv.median()), 1) if len(vv) else None,
                "racha": n, "zona": z,
                "desde": vv.index[0].strftime("%Y-%m") if len(vv) else None,
            })
    env = envolvente(serie)
    env["hoy"] = [None if pd.isna(serie.loc[ultimo, "M%d" % k])
                  else round(float(serie.loc[ultimo, "M%d" % k] / serie.loc[ultimo, "M1"]), 4)
                  for k in range(1, 9)]
    env["precios_hoy"] = [None if pd.isna(serie.loc[ultimo, "M%d" % k])
                          else round(float(serie.loc[ultimo, "M%d" % k]), 4)
                          for k in range(1, 9)]

    datos = {
        "generado": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "ultimo": ultimo.strftime("%Y-%m-%d"),
        "n_sesiones": len(serie),
        "desde": serie.index[0].strftime("%Y-%m-%d"),
        "curva": {("M%d" % k): (None if pd.isna(serie.loc[ultimo, "M%d" % k])
                                else float(serie.loc[ultimo, "M%d" % k]))
                  for k in range(1, 9)},
        "fechas": fechas, "pares": pares, "meta": meta,
        "spx": cargar_spx(pct.index),
        "conv": {c: [None if np.isnan(v) else round(float(v), 1) for v in conv[c].values]
                 for c in conv.columns},
        "base": {c: [None if np.isnan(v) else round(float(v), 1) for v in base[c].values]
                 for c in base.columns},
        "meta_extra": meta_extra,
        "envolvente": env,
        "heat": mapa_calor(pct),
    }
    with open(os.path.join(SITIO, "data.json"), "w", encoding="utf-8") as fh:
        json.dump(datos, fh, separators=(",", ":"))

    validos = hoy.dropna()
    reemplazos = {
        "@@FECHA@@": ultimo.strftime("%d/%m/%Y"),
        "@@GENERADO@@": datos["generado"],
        "@@NSES@@": "{:,}".format(len(serie)).replace(",", "."),
        "@@DESDE@@": serie.index[0].strftime("%b %Y"),
        "@@CURVA@@": " &middot; ".join("%.4g" % v for v in curva.values),
        "@@MAXPAR@@": validos.idxmax(), "@@MAXVAL@@": "%d" % round(validos.max()),
        "@@MINPAR@@": validos.idxmin(), "@@MINVAL@@": "%d" % round(validos.min()),
    }
    html = PLANTILLA
    for k, v in reemplazos.items():
        html = html.replace(k, v)
    with open(os.path.join(SITIO, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(html)

    def kb(p):
        return os.path.getsize(p) / 1024.0

    print("Escrito index.html (%.0f KB) + data.json (%.0f KB)"
          % (kb(os.path.join(SITIO, "index.html")), kb(os.path.join(SITIO, "data.json"))))
    print("  ultimo dia: %s | pares: %d | sesiones: %d"
          % (ultimo.date(), len(meta), len(serie)))


PLANTILLA = u"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<title>VIX FUTURES - estructura temporal (28 pares M1-M8)</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<script src="https://cdn.plot.ly/plotly-2.30.0.min.js"></script>
<style>
:root { --bg:#0d1117; --panel:#161b22; --text:#c9d1d9; --muted:#8b949e; --border:#30363d; --maxw:1560px; }
* { box-sizing:border-box; }
body { margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
       background:var(--bg); color:var(--text); padding:16px; }
.header { max-width:var(--maxw); margin:0 auto 16px auto; display:flex; flex-wrap:wrap;
          align-items:flex-start; justify-content:space-between; gap:12px; }
h1 { font-size:18px; margin:0; font-weight:600; }
.subtitle { color:var(--muted); font-size:13px; margin-top:2px; max-width:86ch; line-height:1.5; }
.latest-group { display:flex; gap:8px; flex-wrap:wrap; }
.latest { background:var(--panel); border:1px solid var(--border); border-radius:6px; padding:9px 12px; min-width:152px; }
.latest .v { font-size:23px; font-weight:700; }
.latest .label { font-size:10.5px; color:var(--muted); text-transform:uppercase; letter-spacing:0.5px; }
.latest .mini { font-size:11px; color:var(--muted); margin-top:3px; }
.dl { max-width:var(--maxw); margin:0 auto 14px auto; display:flex; gap:8px; flex-wrap:wrap; }
.dl a { display:inline-flex; align-items:center; gap:.45em; background:var(--panel); color:var(--text);
        border:1px solid var(--border); border-radius:6px; padding:8px 13px; font-size:12.5px;
        text-decoration:none; font-weight:600; }
.dl a:hover { border-color:#58a6ff; color:#58a6ff; }
.note { max-width:var(--maxw); margin:12px auto; color:var(--muted); font-size:13px; line-height:1.55;
        background:var(--panel); border:1px solid var(--border); border-radius:6px; padding:12px 14px; }
.note strong { color:var(--text); }
.note code { font-family:ui-monospace,Menlo,monospace; background:#21262d; padding:1px 5px;
             border-radius:3px; font-size:12px; color:var(--text); }
.section-title { max-width:var(--maxw); margin:26px auto 6px auto; font-size:16px; font-weight:700;
                 color:var(--text); border-bottom:2px solid var(--border); padding-bottom:6px; }
details.sec { max-width:var(--maxw); margin:14px auto 0 auto; }
details.sec > summary { list-style:none; cursor:pointer; user-select:none;
  display:flex; align-items:center; gap:10px; font-size:16px; font-weight:700;
  color:var(--text); border-bottom:2px solid var(--border); padding:8px 2px;
  background:var(--panel); border-radius:6px 6px 0 0; padding-left:12px; }
details.sec > summary::-webkit-details-marker { display:none; }
details.sec > summary::before { content:'\25B6'; font-size:10px; color:var(--muted);
  transition:transform .15s; display:inline-block; }
details.sec[open] > summary::before { transform:rotate(90deg); }
details.sec > summary:hover { color:#58a6ff; }
details.sec > summary .cnt { margin-left:auto; margin-right:12px; font-size:11px;
  font-weight:500; color:var(--muted); font-family:ui-monospace,Menlo,monospace; }
details.sec[open] > summary { border-radius:6px 6px 0 0; }
.secbody { padding-top:4px; }
.pane { max-width:var(--maxw); margin:0 auto 14px auto; background:var(--panel);
        border:1px solid var(--border); border-radius:6px; padding:10px 12px 4px 12px; }
.pane-head { display:flex; align-items:baseline; justify-content:space-between; gap:12px;
             flex-wrap:wrap; padding:0 2px 6px 2px; }
.pane-par { font-family:ui-monospace,Menlo,monospace; font-size:15px; font-weight:700; letter-spacing:.3px; }
.pane-par .sub { color:var(--muted); font-weight:400; font-size:11.5px; margin-left:8px; letter-spacing:0; }
#vivo { display:none; margin:14px 0 0; padding:10px 14px; border-radius:8px;
  border:1px solid #2d4a2d; background:#121a12; font-size:12px; }
#vivo.rancio { border-color:#4a3a1a; background:#1a1710; }
#vivo .vtit { font-weight:700; letter-spacing:.06em; color:#7ee787; font-size:11px; }
#vivo.rancio .vtit { color:#d29922; }
#vivo .vmeta { color:var(--muted); margin-top:3px; }
#vivo .vpatas { display:flex; gap:10px; flex-wrap:wrap; margin-top:7px; }
#vivo .vp { border:1px solid var(--line); border-radius:5px; padding:2px 7px;
  font-variant-numeric:tabular-nums; }
#vivo .vp b { color:var(--text); }
#vivo .vp.viejo { border-color:#6e4a1a; color:#d29922; }
#vivo .vtri { margin-top:8px; font-family:ui-monospace,Consolas,monospace;
  white-space:pre; font-size:12px; line-height:1.45; }
.pane-kpis { display:flex; gap:14px; align-items:baseline; flex-wrap:wrap; }
.pane-kpi { font-size:11px; color:var(--muted); }
.pane-kpi b { font-size:13px; color:var(--text); font-weight:600; margin-left:4px;
              font-family:ui-monospace,Menlo,monospace; }
.pane-now { font-family:ui-monospace,Menlo,monospace; font-size:26px; font-weight:700; line-height:1; }
.zona { display:inline-block; padding:2px 9px; border-radius:12px; font-size:10.5px; font-weight:600; }
.zona.alta { background:rgba(207,34,46,0.20); color:#f85149; }
.zona.baja { background:rgba(56,139,253,0.18); color:#58a6ff; }
.zona.media { background:rgba(110,118,129,0.20); color:#8b949e; }
.plot { width:100%; height:270px; }
.footer { max-width:var(--maxw); margin:20px auto 0 auto; color:var(--muted); font-size:12px;
          border-top:1px solid var(--border); padding-top:14px; line-height:1.6; }
.footer b { color:var(--text); }
.footer p + p { margin-top:8px; }
.err { padding:30px; text-align:center; color:#f85149; }
</style>
</head>
<body>

<div class="header">
  <div>
    <h1>VIX FUTURES &mdash; estructura temporal</h1>
    <div class="subtitle">Los 8 primeros vencimientos de futuros del VIX cruzados entre si
      (28 pares). Para cada dia, donde queda ese par frente a los dias anteriores que
      estaban <strong>a la misma distancia del vencimiento</strong> &mdash; percentil
      expanding y condicional, sin mirar al futuro. Desde @@DESDE@@, @@NSES@@ sesiones.</div>
  </div>
  <div class="latest-group">
    <div class="latest">
      <div class="label">Ultimo dato</div>
      <div><span class="v">@@FECHA@@</span></div>
      <div class="mini">curva: @@CURVA@@</div>
    </div>
    <div class="latest">
      <div class="label">Par mas alto</div>
      <div><span class="v" style="color:#f85149">@@MAXVAL@@</span></div>
      <div class="mini">@@MAXPAR@@ &middot; lo mas cerca de backwardation</div>
    </div>
    <div class="latest">
      <div class="label">Par mas bajo</div>
      <div><span class="v" style="color:#58a6ff">@@MINVAL@@</span></div>
      <div class="mini">@@MINPAR@@ &middot; lo mas cerca de contango extremo</div>
    </div>
  </div>
</div>

<div id="vivo">
  <div class="vtit" id="vtit">EN VIVO</div>
  <div class="vmeta" id="vmeta"></div>
  <div class="vpatas" id="vpatas"></div>
  <div class="vtri" id="vtri"></div>
</div>

<div class="dl">
  <a href="data/vix_futuros_M1_M8.csv" download>&darr; CSV historico M1-M8</a>
  <a href="data/vix_percentiles_pares.csv" download>&darr; CSV percentiles (28 pares)</a>
  <a href="data.json" download>&darr; data.json</a>
  <a href="https://github.com/manumartinb/vix-term-structure" target="_blank" rel="noopener">Repo</a>
</div>

<div class="note">
  <strong>COMO LEERLO</strong>: cada panel es un par <code>Mj/Mi</code>. El numero grande es
  el percentil de HOY. <strong>Cerca de 100</strong> = ese tramo de la curva esta lo mas
  cerca posible de <strong>backwardation</strong> (el vencimiento corto caro respecto al
  largo): regimen de estres, theta a favor del vendedor de front month.
  <strong>Cerca de 0</strong> = <strong>contango extremo</strong> (el largo mucho mas caro):
  regimen de calma, theta en contra.
  <br><strong>METODO</strong>: ratio del par = <code>Mj/Mi - 1</code>. Su percentil se calcula
  <em>expanding</em> contra la historia anterior a esa fecha (cero lookahead) y se
  invierte, igual que en el panel original.
  <br><strong>CORRECCION POR DIAS A VENCIMIENTO (importante para leerlo bien)</strong>: el
  percentil NO se calcula contra toda la historia mezclada, sino <strong>solo contra los
  dias que estaban en el mismo punto del ciclo mensual</strong> (mismos dias hasta el
  vencimiento del front month, ventana &plusmn;2). Sin esto el panel medía el calendario y
  no el mercado: como M1 converge al contado segun se acerca su vencimiento, la base
  promediaba percentil 27 a 3 dias del roll y 61 a 30 dias &mdash; <strong>33,9 puntos de
  sesgo puramente mecanico</strong>, y los pares con M1 arrastraban 13,0. Con la correccion
  el sesgo baja a <strong>0,4 y 0,8</strong>.
  <br>Consecuencia para la lectura: <strong>un 95 significa "extremo PARA ESTE PUNTO DEL
  CICLO"</strong>, no extremo en terminos absolutos. Es lo que se quiere para leer regimen,
  pero no es lo mismo. Los dias con menos de 100 observaciones comparables no se publican.
  Cada grafico lleva selector de rango y zoom. La linea gris tenue del fondo es el
  <strong>SPX</strong> (eje derecho, escala propia): sirve para leer cada tramo de la curva
  contra lo que hacia el indice.
  <br><strong>DATOS</strong>: precio de <strong>liquidacion</strong> (settlement), no de cierre
  &mdash; difieren en 3 de cada 4 dias. De abr-2007 a ago-2018, el archivo publico del Cboe
  Futures Exchange contrato a contrato, <strong>salvo 16 contratos</strong> (los de sep, oct,
  nov y dic de 2014 a 2017) que ese archivo no sirve y vienen de TradingView. De sep-2018 en
  adelante, TradingView, que publica el mismo precio de liquidacion.
  <br><strong>VERIFICACION INDEPENDIENTE</strong>: (a) contra el <em>endpoint</em> oficial de
  settlement del exchange, en las fechas recientes que todavia sirve (15-jun, 15-jul, 14-ago y
  18-sep de 2026), coincide <strong>al cuarto decimal</strong> en M1..M4; (b) contra una serie
  construida aparte por el autor a partir de <em>scraping</em> de VIXCENTRAL, <strong>94,3% de
  celdas identicas</strong>, y de 2010 a 2013 <strong>sin una sola diferencia</strong> en ~1.790
  sesiones seguidas.
</div>

<details class="sec" data-sec="envol">
  <summary>La curva de hoy contra su historia <span class="cnt">1 grafico</span></summary>
  <div class="secbody">
    <div class="note" style="margin-top:6px">Los 8 vencimientos de hoy, <strong>divididos
      por su propio M1</strong> para quitar el nivel y dejar solo la FORMA (si no, el
      abanico lo dominaria que el VIX este en 12 o en 80). Detras, donde ha estado esa
      forma el 90% y el 50% del tiempo desde 2007. Si la linea naranja se sale de la banda
      ancha, la curva de hoy tiene una forma que casi no se ha visto.</div>
    <div class="pane"><div id="envol" class="plot" style="height:330px"></div></div>
  </div>
</details>

<details class="sec" data-sec="heat">
  <summary>Mapa de calor: los 28 pares a lo largo del tiempo <span class="cnt">1 grafico</span></summary>
  <div class="secbody">
    <div class="note" style="margin-top:6px">Cada fila es un par, el tiempo va de izquierda
      a derecha. <strong>Rojo = percentil alto</strong> (ese tramo, del reves),
      <strong>azul = bajo</strong> (contango extremo). Sirve para ver de un vistazo si la
      curva se rompe ENTERA (manchon vertical rojo: 2008, 2018, 2020) o solo por un tramo.
      Agregado a semanal para que la rejilla sea manejable.</div>
    <div class="pane"><div id="heat" class="plot" style="height:520px"></div></div>
  </div>
</details>

<details class="sec" data-sec="panes">
  <summary>Los 28 pares de la curva <span class="cnt">28 paneles</span></summary>
  <div class="secbody"><div id="panes"></div></div>
</details>

<details class="sec" data-sec="conv">
  <summary>Convexidad: la panza de la curva <span class="cnt">6 paneles</span></summary>
  <div class="secbody">
    <div class="note" style="margin-top:6px">Curvatura de cada trio de vencimientos
      consecutivos: <code>P(n) &minus; 2&middot;P(n+1) + P(n+2)</code>. <strong>OJO, esta
      familia NO va invertida</strong> (se respeta la convencion del panel original):
      <strong>percentil alto = curva concava</strong>, con el decay a favor del vendedor de
      front month; <strong>bajo = convexa</strong>, decay en contra.</div>
    <div id="panes-conv"></div>
  </div>
</details>

<details class="sec" data-sec="base">
  <summary>Base: la curva contra el VIX al contado <span class="cnt">1 panel</span></summary>
  <div class="secbody">
    <div class="note" style="margin-top:6px">
      <strong>QUE ES.</strong> En vez de mirar el futuro mas cercano &mdash; que cada dia
      es un dia mas viejo y acaba muriendo pegado al contado &mdash; se fabrica un futuro
      <strong>sintetico de 30 dias clavados</strong>, interpolando entre M1 y M2 con el
      peso <code>(DTE2&minus;30)/(DTE2&minus;DTE1)</code>. Ese sintetico nunca envejece, asi
      que no tiene roll ni salto mensual. <code>BASE_CM30</code> es lo que ese futuro de 30
      dias cuesta por encima del VIX al contado.
      <br><strong>COMO SE LEE.</strong> Va invertida, como el resto del panel.
      <strong>Cerca de 100</strong>: el contado caro respecto al futuro &mdash;
      <em>backwardation</em>, panico, el mercado paga por protegerse HOY y no dentro de un
      mes. <strong>Cerca de 0</strong>: el futuro mucho mas caro que el contado &mdash;
      <em>contango</em> pronunciado, calma, nadie teme nada a un mes vista. Y como el
      percentil es condicional, un 95 quiere decir "extremo <strong>para este punto del
      ciclo mensual</strong>", no extremo en absoluto.
      <br><strong>POR QUE SOLO ESTA.</strong> Antes habia tres (M1/contado, la misma
      dividida por los dias que quedaban, y esta). Medidas entre si daban correlacion de
      <strong>0,85 a 0,94</strong> y coincidian de zona el 78-87% de los dias: eran la
      misma senal repetida. El desempate fue el dia del roll &mdash; el salto medio de una
      jornada a otra sube a 18,9 y 19,3 en las dos primeras cuando quedan 2 dias o menos
      para el vencimiento (un 55-60% mas nerviosas que de costumbre), mientras que esta se
      queda en <strong>8,5</strong>, mas tranquila incluso que un dia normal. Es la unica
      inmune, por construccion. Es ademas el estandar del sector, comparable con cualquier
      indice publicado fuera, y la misma ponderacion que ya hacia la celda H25 del panel
      original.
      <br><strong>OJO AL INTERPRETARLA.</strong> La zona alta es relativamente clara: el
      panico historicamente revierte. La zona baja es <strong>ambigua a proposito</strong>:
      un contango extremo significa a la vez que el carry de vender volatilidad esta en
      maximos y que el mercado esta complaciente, que es el estado desde el que saltan los
      sustos. Son dos lecturas opuestas y cual domina depende del horizonte. Nada de esto
      esta comprobado todavia sobre estos datos.</div>
    <div id="panes-base"></div>
  </div>
</details>

<div class="footer">
  <p><b>Roll</b>: un contrato deja de ser M1 el mismo dia en que liquida. <b>Dias sin sesion</b>:
  filtrados con el calendario real del CFE (algunos proveedores emiten barra para la sesion
  nocturna del domingo, que no es dia de liquidacion). <b>Reescalado de 2007</b>: hasta el
  23-mar-2007 los futuros del VIX cotizaban a diez veces el indice; esta serie arranca despues
  del cambio para no mezclar escalas.</p>
  <p>Generado el @@GENERADO@@ &middot; datos de mercado publicos, sin garantia de exactitud y
  sin fin comercial &middot; esto no es asesoramiento de inversion.</p>
</div>

<script>
const LAYOUT_BASE = {
  paper_bgcolor:'#161b22', plot_bgcolor:'#161b22',
  font:{ color:'#c9d1d9', family:'-apple-system, sans-serif', size:11 },
  height:270, margin:{ t:30, r:16, b:34, l:44 },
  showlegend:true, hovermode:'x unified',
  legend:{ orientation:'h', x:1, xanchor:'right', y:1.20, font:{ size:10 },
           bgcolor:'rgba(0,0,0,0)' },
  xaxis:{ gridcolor:'#21262d', linecolor:'#30363d', type:'date',
    rangeselector:{ buttons:[
      { count:90, label:'90D', step:'day', stepmode:'backward' },
      { count:1, label:'1A', step:'year', stepmode:'backward' },
      { count:3, label:'3A', step:'year', stepmode:'backward' },
      { count:10, label:'10A', step:'year', stepmode:'backward' },
      { step:'all', label:'All' } ],
      bgcolor:'#21262d', activecolor:'#6d28d9', font:{ color:'#c9d1d9', size:10 },
      bordercolor:'#30363d', borderwidth:1, y:1.18, x:0 },
    rangeslider:{ visible:false } },
  yaxis:{ range:[0,100], gridcolor:'#21262d', linecolor:'#30363d',
    tickvals:[0,5,20,50,80,95,100], tickfont:{ size:10 } },
  // Eje derecho SOLO para el SPX: escala propia (autorange) para que el nivel del
  // indice no aplaste la escala de percentiles. Sin rejilla, para no ensuciar el fondo.
  yaxis2:{ overlaying:'y', side:'right', autorange:true, showgrid:false,
    linecolor:'#30363d', tickfont:{ color:'#8b949e', size:9 } },
  shapes:[
    { type:'rect', xref:'paper', x0:0, x1:1, yref:'y', y0:95, y1:100,
      fillcolor:'#f85149', opacity:0.10, line:{ width:0 }, layer:'below' },
    { type:'rect', xref:'paper', x0:0, x1:1, yref:'y', y0:0, y1:5,
      fillcolor:'#58a6ff', opacity:0.10, line:{ width:0 }, layer:'below' },
    { type:'line', xref:'paper', x0:0, x1:1, yref:'y', y0:50, y1:50,
      line:{ color:'#30363d', width:1, dash:'dot' }, layer:'below' }
  ]
};

function esc(s){
  return String(s).replace(/[&<>"']/g, c =>
    ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

async function load(){
  let d;
  try { d = await (await fetch('data.json?v=' + Date.now())).json(); }
  catch(e){
    document.getElementById('panes').innerHTML =
      '<div class="err">No se pudo cargar data.json</div>';
    return;
  }

  const cont = document.getElementById('panes');
  cont.innerHTML = '';
  const zonaTxt = { alta:'zona alta', baja:'zona baja', media:'zona media' };

  d.meta.forEach(m => {
    const el = document.createElement('div');
    el.className = 'pane';
    el.innerHTML =
      '<div class="pane-head">' +
        '<div class="pane-par">' + esc(m.par) +
          '<span class="sub">M' + m.j + ' frente a M' + m.i +
          ' &middot; desde ' + esc(m.desde || '--') + '</span></div>' +
        '<div class="pane-kpis">' +
          '<span class="pane-kpi">mediana<b>' +
            (m.mediana !== null ? m.mediana : '--') + '</b></span>' +
          '<span class="zona ' + m.zona + '">' + m.racha + ' dias en ' +
            zonaTxt[m.zona] + '</span>' +
          '<span class="pane-now" style="color:' + m.color + '">' +
            (m.hoy !== null ? Math.round(m.hoy) : '--') + '</span>' +
        '</div>' +
      '</div>' +
      '<div class="plot" id="plot-' + m.par.replace('/','_') + '"></div>';
    cont.appendChild(el);
  });

  // 28 graficos no se pintan de golpe: se dibujan segun entran en pantalla
  const pintado = new Set();
  const io = new IntersectionObserver((entries) => {
    entries.forEach(en => {
      if (!en.isIntersecting) return;
      const id = en.target.id;
      if (pintado.has(id)) return;
      pintado.add(id);
      const par = id.replace('plot-','').replace('_','/');
      const trazas = [];
      // El SPX va PRIMERO para que quede por debajo, tenue, como referencia de fondo.
      if (d.spx) trazas.push({
        x: d.fechas, y: d.spx, type:'scatter', mode:'lines', name:'SPX',
        yaxis:'y2', line:{ color:'#8b949e', width:1 }, opacity:0.55, connectgaps:false,
        hovertemplate:'<b>SPX</b>: %{y:,.0f}<extra></extra>'
      });
      trazas.push({
        x: d.fechas, y: d.pares[par], type:'scatter', mode:'lines', name:par,
        line:{ color:'#58a6ff', width:1.2 }, connectgaps:false,
        hovertemplate:'%{x|%d %b %Y}<br><b>%{y:.1f}</b><extra></extra>'
      });
      Plotly.newPlot(id, trazas, LAYOUT_BASE, { responsive:true, displaylogo:false,
        modeBarButtonsToRemove:['lasso2d','select2d','autoScale2d'] });
      io.unobserve(en.target);
    });
  }, { rootMargin:'400px 0px' });

  // Cada seccion dibuja sus graficos la PRIMERA vez que se abre. Asi la pagina
  // carga sin un solo grafico y el navegador no se arrastra con 36 de golpe.
  const pintores = {};
  function alAbrir(nombre, fn) { pintores[nombre] = fn; }
  document.querySelectorAll('details.sec').forEach(det => {
    det.addEventListener('toggle', () => {
      if (!det.open || det.dataset.hecho) return;
      det.dataset.hecho = '1';
      const fn = pintores[det.dataset.sec];
      if (fn) fn();
    });
  });

  // ---- envolvente: forma de hoy contra su abanico historico
  alAbrir('envol', () => {
  if (d.envolvente) {
    const e = d.envolvente, xs = [1,2,3,4,5,6,7,8].map(k => 'M' + k);
    const banda = (lo, hi, color, nombre) => ([
      { x: xs, y: e[lo], type:'scatter', mode:'lines', line:{ width:0 },
        hoverinfo:'skip', showlegend:false },
      { x: xs, y: e[hi], type:'scatter', mode:'lines', line:{ width:0 },
        fill:'tonexty', fillcolor:color, name:nombre,
        hovertemplate:'%{x}: %{y:.3f}<extra>' + nombre + '</extra>' }
    ]);
    Plotly.newPlot('envol', [].concat(
      banda('p05','p95','rgba(88,166,255,0.10)','90% del tiempo'),
      banda('p25','p75','rgba(88,166,255,0.20)','50% del tiempo'),
      [{ x: xs, y: e.p50, type:'scatter', mode:'lines', name:'mediana historica',
         line:{ color:'#8b949e', width:1.4, dash:'dot' },
         hovertemplate:'%{x}: %{y:.3f}<extra>mediana</extra>' },
       { x: xs, y: e.hoy, type:'scatter', mode:'lines+markers', name:'HOY',
         line:{ color:'#f0883e', width:2.6 }, marker:{ size:7 },
         text: e.precios_hoy,
         hovertemplate:'%{x}: %{y:.3f} x M1<br>precio %{text}<extra>HOY</extra>' }]
    ), Object.assign({}, LAYOUT_BASE, {
      height:330, xaxis:{ gridcolor:'#21262d', linecolor:'#30363d', type:'category' },
      yaxis:{ autorange:true, gridcolor:'#21262d', linecolor:'#30363d',
              title:{ text:'veces M1', font:{ size:11 } }, tickfont:{ size:10 } },
      yaxis2:{ visible:false }, shapes:[],
      legend:{ orientation:'h', x:0, y:1.13, font:{ size:10 }, bgcolor:'rgba(0,0,0,0)' }
    }), { responsive:true, displaylogo:false });
  }

  });

  // ---- mapa de calor
  alAbrir('heat', () => {
  if (d.heat) {
    Plotly.newPlot('heat', [{
      z: d.heat.z, x: d.heat.fechas, y: d.heat.pares, type:'heatmap',
      zmin:0, zmax:100, colorscale:[
        [0,'#1f4b7a'], [0.2,'#2e5b8c'], [0.45,'#21262d'],
        [0.55,'#21262d'], [0.8,'#b3454a'], [1,'#f85149']],
      colorbar:{ thickness:10, len:0.85, tickfont:{ size:9, color:'#8b949e' },
                 outlinewidth:0 },
      hovertemplate:'%{y}<br>%{x|%b %Y}<br><b>%{z}</b><extra></extra>'
    }], Object.assign({}, LAYOUT_BASE, {
      height:520, margin:{ t:16, r:16, b:34, l:64 }, showlegend:false,
      xaxis:{ gridcolor:'#21262d', linecolor:'#30363d', type:'date' },
      yaxis:{ autorange:'reversed',
              tickfont:{ size:9, family:'ui-monospace,Menlo,monospace' },
              gridcolor:'#21262d', linecolor:'#30363d' },
      yaxis2:{ visible:false }, shapes:[]
    }), { responsive:true, displaylogo:false });
  }

  });

  // ---- familias extra (convexidad y base) con el mismo formato de panel
  const pintarFamilia = (fam) => {
  if (d.meta_extra) {
    const destinos = { conv:'panes-conv', base:'panes-base' };
    d.meta_extra.forEach(m => {
      if (m.fam !== fam) return;
      const cont2 = document.getElementById(destinos[m.fam]);
      if (!cont2) return;
      const el = document.createElement('div');
      el.className = 'pane';
      el.innerHTML =
        '<div class="pane-head">' +
          '<div class="pane-par">' + esc(m.par) +
            '<span class="sub">desde ' + esc(m.desde || '--') + '</span></div>' +
          '<div class="pane-kpis">' +
            '<span class="pane-kpi">mediana<b>' +
              (m.mediana !== null ? m.mediana : '--') + '</b></span>' +
            '<span class="zona ' + m.zona + '">' + m.racha + ' dias en ' +
              zonaTxt[m.zona] + '</span>' +
            '<span class="pane-now" style="color:' + m.color + '">' +
              (m.hoy !== null ? Math.round(m.hoy) : '--') + '</span>' +
          '</div>' +
        '</div>' +
        '<div class="plot" id="x-' + m.fam + '-' + m.par + '"></div>';
      cont2.appendChild(el);
      const trazas = [];
      if (d.spx) trazas.push({
        x: d.fechas, y: d.spx, type:'scatter', mode:'lines', name:'SPX',
        yaxis:'y2', line:{ color:'#8b949e', width:1 }, opacity:0.55, connectgaps:false,
        hovertemplate:'<b>SPX</b>: %{y:,.0f}<extra></extra>' });
      trazas.push({
        x: d.fechas, y: d[m.fam][m.par], type:'scatter', mode:'lines', name:m.par,
        line:{ color: m.fam === 'conv' ? '#a78bfa' : '#3fb950', width:1.2 },
        connectgaps:false,
        hovertemplate:'%{x|%d %b %Y}<br><b>%{y:.1f}</b><extra></extra>' });
      Plotly.newPlot('x-' + m.fam + '-' + m.par, trazas, LAYOUT_BASE,
        { responsive:true, displaylogo:false,
          modeBarButtonsToRemove:['lasso2d','select2d','autoScale2d'] });
    });
  }

  };
  alAbrir('conv', () => pintarFamilia('conv'));
  alAbrir('base', () => pintarFamilia('base'));

  // los 28 paneles: el DOM ya esta puesto; al abrir la seccion se observan y
  // se dibujan solo los que entran en pantalla
  alAbrir('panes', () => {
    document.querySelectorAll('#panes .plot').forEach(p => io.observe(p));
  });
}
load();
</script>
<script>
/* Banda EN VIVO. Lee vivo.json, que una tarea reescribe cada 15 min durante la
   sesion. Si no existe (fuera de mercado, o la tarea parada) la banda no se
   ensena: es preferible ausencia a un numero viejo con pinta de fresco. */
(function () {
  var MAX_VIDA_MIN = 45;   /* pasado esto el propio vivo.json es viejo */

  function pinta(v) {
    var caja = document.getElementById('vivo');
    var edadJson = (Date.now() - new Date(v.momento.replace(' ', 'T')).getTime()) / 60000;
    var rancio = edadJson > MAX_VIDA_MIN;
    caja.className = rancio ? 'rancio' : '';
    document.getElementById('vtit').textContent =
      rancio ? 'ULTIMA LECTURA (no es de ahora mismo)' : 'EN VIVO';

    document.getElementById('vmeta').innerHTML =
      'Los ' + v.patas_total + ' vencimientos pedidos <strong>a la vez</strong> el ' +
      v.momento + ', en una ventana de ' + v.ventana_s + ' s. ' +
      v.patas_en_vivo + ' de ' + v.patas_total + ' con precio del momento. ' +
      'Comparado contra el historico cerrado hasta el ' + v.historia_hasta +
      ' (' + v.dte_m1 + ' dias al vencimiento del M1). ' +
      '<strong>Provisional</strong>: el dato firme es el settlement de esta noche.';

    var h = '';
    v.patas.forEach(function (p) {
      var viejo = p.origen !== 'vivo';
      var det = p.origen === 'vivo' ? (Math.round(p.edad_min) + ' min')
              : (p.origen === 'cierre_previo' ? 'cierre previo' : 'sin dato');
      h += '<span class="vp' + (viejo ? ' viejo' : '') + '">' + p.hueco + ' <b>' +
           (p.precio == null ? '--' : p.precio.toFixed(3)) + '</b> ' + det + '</span>';
    });
    document.getElementById('vpatas').innerHTML = h;

    var t = '     ' + [2,3,4,5,6,7,8].map(function (j) {
      return ('   M' + j).slice(-4); }).join('') + '\n';
    for (var i = 1; i <= 7; i++) {
      t += ('M' + i + '   ').slice(0, 4) + ' ';
      for (var j = 2; j <= 8; j++) {
        if (j <= i) { t += '    '; continue; }
        var d = v.pares['M' + j + '/M' + i];
        if (!d || d.pct == null) { t += '   .'; continue; }
        var n = String(Math.round(d.pct));
        t += ('   ' + n + (d.fiable ? '' : '*')).slice(-4);
      }
      t += '\n';
    }
    document.getElementById('vtri').textContent = t.replace(/\n$/, '');
    caja.style.display = 'block';
  }

  function carga() {
    fetch('vivo.json?t=' + Date.now())
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (v) { if (v) pinta(v); })
      .catch(function () { /* sin vivo.json no se ensena nada */ });
  }
  carga();
  setInterval(carga, 5 * 60 * 1000);
})();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
