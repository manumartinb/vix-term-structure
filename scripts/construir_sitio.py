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

Los primeros MIN_HISTORIA dias no se publican: un percentil contra 20
observaciones no significa nada.

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

MIN_HISTORIA = 250
UMB_ALTO, UMB_BAJO = 95.0, 5.0

VERDE, AMBAR, ROJO, AZUL = "#3fb950", "#d29922", "#f85149", "#58a6ff"


def percentil_expanding(valores):
    """Percentil (0-100, invertido) de cada valor contra TODOS los anteriores.
    Lista ordenada incremental: O(n log n), no O(n^2)."""
    orden = []
    out = np.full(len(valores), np.nan)
    for i, v in enumerate(valores):
        if not np.isnan(v):
            if len(orden) >= MIN_HISTORIA:
                if v < orden[0]:
                    p = 0.0
                elif v > orden[-1]:
                    p = 1.0
                else:
                    p = bisect.bisect_left(orden, v) / float(len(orden) - 1)
                out[i] = (1.0 - p) * 100.0
            bisect.insort(orden, v)
    return out


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


def main():
    os.makedirs(SITIO_DATA, exist_ok=True)
    serie = pcv.cargar_serie()
    r = pcv.ratios(serie)

    print("Percentil expanding de %d pares sobre %d sesiones..." % (len(r.columns), len(r)))
    pct = pd.DataFrame(index=r.index)
    for col in r.columns:
        pct[col] = percentil_expanding(r[col].values)

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
        "@@MINHIST@@": str(MIN_HISTORIA),
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
.pane { max-width:var(--maxw); margin:0 auto 14px auto; background:var(--panel);
        border:1px solid var(--border); border-radius:6px; padding:10px 12px 4px 12px; }
.pane-head { display:flex; align-items:baseline; justify-content:space-between; gap:12px;
             flex-wrap:wrap; padding:0 2px 6px 2px; }
.pane-par { font-family:ui-monospace,Menlo,monospace; font-size:15px; font-weight:700; letter-spacing:.3px; }
.pane-par .sub { color:var(--muted); font-weight:400; font-size:11.5px; margin-left:8px; letter-spacing:0; }
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
      (28 pares). Para cada dia, donde queda ese par dentro de <strong>toda su historia
      previa</strong> &mdash; percentil expanding, sin mirar al futuro. Desde @@DESDE@@,
      @@NSES@@ sesiones.</div>
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
  <em>expanding</em> contra toda la historia anterior a esa fecha (cero lookahead) y se
  invierte, igual que en el panel original. Los primeros <strong>@@MINHIST@@ dias</strong> de
  cada serie no se publican: un percentil contra tan pocas observaciones no significa nada.
  Cada grafico lleva selector de rango y zoom. La linea gris tenue del fondo es el
  <strong>SPX</strong> (eje derecho, escala propia): sirve para leer cada tramo de la curva
  contra lo que hacia el indice.
  <br><strong>DATOS</strong>: precio de <strong>liquidacion</strong> (settlement), no de cierre
  &mdash; difieren en 3 de cada 4 dias. Abr-2007 a ago-2018 del archivo oficial del Cboe
  Futures Exchange, contrato a contrato; de sep-2018 en adelante via TradingView, que publica
  el mismo settlement. Validado contra el settlement oficial en las 2.436 sesiones en que el
  exchange lo sirve: <strong>98,77% de coincidencia exacta</strong>.
</div>

<div class="section-title">Los 28 pares de la curva</div>
<div id="panes"><div class="err">Cargando...</div></div>

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
        x: d.fechas, y: d.spx, type:'scattergl', mode:'lines', name:'SPX',
        yaxis:'y2', line:{ color:'#8b949e', width:1 }, opacity:0.55, connectgaps:false,
        hovertemplate:'<b>SPX</b>: %{y:,.0f}<extra></extra>'
      });
      trazas.push({
        x: d.fechas, y: d.pares[par], type:'scattergl', mode:'lines', name:par,
        line:{ color:'#58a6ff', width:1.2 }, connectgaps:false,
        hovertemplate:'%{x|%d %b %Y}<br><b>%{y:.1f}</b><extra></extra>'
      });
      Plotly.newPlot(id, trazas, LAYOUT_BASE, { responsive:true, displaylogo:false,
        modeBarButtonsToRemove:['lasso2d','select2d','autoScale2d'] });
      io.unobserve(en.target);
    });
  }, { rootMargin:'400px 0px' });

  document.querySelectorAll('.plot').forEach(p => io.observe(p));
}
load();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
