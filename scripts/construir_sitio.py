"""
construir_sitio.py -- genera el sitio estatico de la curva de futuros VIX.

SALIDA (carpeta sitio/):
  index.html                      panel con los 28 pares MX/MY
  data/vix_futuros_M1_M8.csv      la serie completa (link de descarga)
  data/vix_percentiles_pares.csv  el percentil expanding de cada par, dia a dia

QUE PINTA
Un panel por cada par (28 en total). Cada panel lleva el percentil EXPANDING:
para cada dia, donde queda el ratio Mj/Mi de ese dia dentro de TODA su historia
previa (nada de mirar al futuro). Convencion heredada del VIX Studio:
  100 = backwardation extrema (curva del reves)   0 = contango extremo

Los primeros MIN_HISTORIA dias no se pintan: un percentil contra 20 observaciones
no significa nada. Se dice en la pagina.

Graficos en SVG generado aqui (sin librerias JS): la pagina abre al instante y
funciona sin conexion. Se submuestrea a MAX_PUNTOS por grafico para que el HTML
no se dispare; el CSV que se descarga lleva TODOS los dias.

Reglas tecnicas del proyecto: ASCII, cp1252.
"""

import os
import bisect
import datetime as dt

import numpy as np
import pandas as pd

import percentiles_curva as pcv

HERE = os.path.dirname(os.path.abspath(__file__))
SITIO = os.path.join(HERE, "sitio")
SITIO_DATA = os.path.join(SITIO, "data")

MIN_HISTORIA = 250      # dias antes de emitir el primer percentil
MAX_PUNTOS = 900        # submuestreo del grafico (el CSV va completo)
W, H = 340, 96          # tamano del area de dibujo de cada panel
UMB_ALTO, UMB_BAJO = 95, 5


def percentil_expanding(valores):
    """Percentil (0-100, ya invertido) de cada valor contra TODOS los anteriores.

    Usa una lista ordenada incremental: O(n log n) en vez de O(n^2)."""
    orden = []
    out = np.full(len(valores), np.nan)
    for i, v in enumerate(valores):
        if not np.isnan(v):
            if len(orden) >= MIN_HISTORIA:
                menores = bisect.bisect_left(orden, v)
                if v < orden[0]:
                    p = 0.0
                elif v > orden[-1]:
                    p = 1.0
                else:
                    p = menores / float(len(orden) - 1)
                out[i] = (1.0 - p) * 100.0
            bisect.insort(orden, v)
    return out


def color_pct(v):
    if np.isnan(v):
        return "#9AA5B1"
    if v >= UMB_ALTO:
        return "#9A3B3B"
    if v <= UMB_BAJO:
        return "#2E5B8C"
    if v >= 75:
        return "#B4653F"
    if v <= 25:
        return "#3D7290"
    return "#5B6672"


def svg_panel(fechas, serie):
    """Grafico SVG del percentil expanding a lo largo del tiempo."""
    m = ~np.isnan(serie)
    if m.sum() < 2:
        return '<div class="nodata">sin historia suficiente</div>'
    f = fechas[m]
    s = serie[m]
    if len(s) > MAX_PUNTOS:
        idx = np.linspace(0, len(s) - 1, MAX_PUNTOS).astype(int)
        f, s = f[idx], s[idx]

    x0 = f[0].value
    span = max(1, f[-1].value - x0)
    pts = []
    for fi, si in zip(f, s):
        x = (fi.value - x0) / span * W
        y = H - (si / 100.0) * H
        pts.append("%.0f,%.1f" % (x, y))

    # bandas de extremo (95 arriba, 5 abajo) y mediana
    y95 = H - 0.95 * H
    y05 = H - 0.05 * H
    y50 = H * 0.5
    return (
        '<svg viewBox="0 0 %d %d" preserveAspectRatio="none" class="spark">'
        '<rect x="0" y="0" width="%d" height="%.1f" class="zona-alta"/>'
        '<rect x="0" y="%.1f" width="%d" height="%.1f" class="zona-baja"/>'
        '<line x1="0" y1="%.1f" x2="%d" y2="%.1f" class="mediana"/>'
        '<polyline points="%s"/>'
        '</svg>'
        % (W, H, W, y95, y05, W, H - y05, y50, W, y50, " ".join(pts))
    )


def main():
    os.makedirs(SITIO_DATA, exist_ok=True)
    serie = pcv.cargar_serie()
    r = pcv.ratios(serie)

    print("Calculando percentil expanding de %d pares sobre %d dias..."
          % (len(r.columns), len(r)))
    pct = pd.DataFrame(index=r.index)
    for col in r.columns:
        pct[col] = percentil_expanding(r[col].values)

    fechas = r.index
    # El titular usa el ultimo dia con la curva COMPLETA. La sesion en curso suele no
    # tener aun el vencimiento mas lejano, y con dropna(how="all") los 7 pares que
    # cuelgan de M8 salian todos "--".
    completos = pct.dropna()
    ultimo = completos.index[-1] if len(completos) else pct.dropna(how="all").index[-1]
    hoy = pct.loc[ultimo]
    curva = serie.loc[ultimo].dropna()

    # ficheros de datos
    serie.reset_index().to_csv(os.path.join(SITIO_DATA, "vix_futuros_M1_M8.csv"),
                               index=False)
    pct.round(2).reset_index().to_csv(
        os.path.join(SITIO_DATA, "vix_percentiles_pares.csv"), index=False)

    # paneles ordenados: primero los de M1, luego M2...
    paneles = []
    for i, j in pcv.PAREJAS:
        col = "M%d/M%d" % (j, i)
        v = hoy.get(col, np.nan)
        s = pct[col].values
        val = pct[col].dropna()
        paneles.append(
            '<figure class="panel">'
            '<figcaption><span class="par">%s</span>'
            '<span class="val" style="color:%s">%s</span></figcaption>'
            '%s'
            '<div class="pie"><span>%s</span><span>mediana %d</span>'
            '<span>%s</span></div>'
            '</figure>'
            % (col, color_pct(v), "--" if np.isnan(v) else "%d" % round(v),
               svg_panel(fechas, s),
               fechas[~np.isnan(s)][0].strftime("%Y") if (~np.isnan(s)).any() else "",
               round(val.median()) if len(val) else 0,
               ultimo.strftime("%Y"))
        )

    validos = hoy.dropna()
    html = PLANTILLA % {
        "fecha": ultimo.strftime("%d/%m/%Y"),
        "generado": dt.datetime.now().strftime("%d/%m/%Y %H:%M"),
        "n_dias": len(serie),
        "desde": serie.index[0].strftime("%b %Y"),
        "curva": "  ".join("%.4g" % v for v in curva.values),
        "maxpar": validos.idxmax(), "maxval": round(validos.max()),
        "minpar": validos.idxmin(), "minval": round(validos.min()),
        "paneles": "\n".join(paneles),
        "min_hist": MIN_HISTORIA,
    }
    with open(os.path.join(SITIO, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(html)

    print("Escrito %s" % os.path.join(SITIO, "index.html"))
    print("  paneles: %d | ultimo dia: %s | tamano html: %.0f KB"
          % (len(paneles), ultimo.date(),
             os.path.getsize(os.path.join(SITIO, "index.html")) / 1024.0))


PLANTILLA = u"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Curva de futuros VIX - percentil historico por par</title>
<style>
:root{
  --paper:#F7F8FA; --card:#FFFFFF; --ink:#131A22; --body:#495460;
  --mute:#7C8894; --rule:#E1E6EC; --rule2:#C8D0D9;
  --alto:#9A3B3B; --bajo:#2E5B8C;
  --sans:"Public Sans","Segoe UI",system-ui,sans-serif;
  --mono:"IBM Plex Mono","Cascadia Mono",Consolas,monospace;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--paper);color:var(--ink);font-family:var(--sans);
  font-size:16px;line-height:1.5;-webkit-font-smoothing:antialiased}
.wrap{max-width:1320px;margin:0 auto;padding:0 clamp(16px,3.5vw,48px)}
header{padding-block:clamp(36px,6vw,72px) clamp(24px,4vw,44px)}
.kicker{font-family:var(--mono);font-size:11px;font-weight:600;letter-spacing:.18em;
  text-transform:uppercase;color:var(--mute)}
h1{font-size:clamp(28px,4.5vw,44px);line-height:1.08;letter-spacing:-.02em;
  margin-top:.5em;max-width:20ch;font-weight:700}
.lede{margin-top:.8em;font-size:clamp(15px,1.5vw,18px);color:var(--body);max-width:62ch}
.barra{display:flex;flex-wrap:wrap;gap:10px;margin-top:clamp(20px,3vw,30px)}
.btn{display:inline-flex;align-items:center;gap:.5em;background:var(--ink);color:#fff;
  text-decoration:none;font-weight:600;font-size:14px;padding:11px 18px;border-radius:3px}
.btn.sec{background:var(--card);color:var(--ink);border:1px solid var(--rule2)}
.btn:hover{opacity:.87}
.figs{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));
  margin-top:clamp(26px,4vw,40px);border-top:1px solid var(--rule2)}
.figs>div{padding:18px 22px 14px;border-right:1px solid var(--rule)}
.figs>div:first-child{padding-left:0}
.figs>div:last-child{border-right:0}
.figs .k{display:block;font-family:var(--mono);font-size:10.5px;font-weight:600;
  letter-spacing:.15em;text-transform:uppercase;color:var(--mute);margin-bottom:.7em}
.figs .n{display:block;font-family:var(--mono);font-size:clamp(20px,2.6vw,30px);
  font-weight:500;letter-spacing:-.02em}
.figs .s{display:block;margin-top:.5em;font-size:13.5px;color:var(--body)}
hr{border:0;height:1px;background:var(--rule2)}
section{padding-block:clamp(30px,5vw,54px)}
h2{font-size:clamp(20px,2.6vw,28px);letter-spacing:-.015em}
.sub{margin-top:.6em;color:var(--body);max-width:70ch;font-size:15px}
.leyenda{display:flex;flex-wrap:wrap;gap:8px 14px;margin-top:18px;font-size:13px;color:var(--body)}
.leyenda b{display:inline-flex;align-items:center;gap:.45em;font-weight:500}
.sw{width:11px;height:11px;border-radius:2px;display:inline-block}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));
  gap:14px;margin-top:clamp(22px,3vw,32px)}
.panel{background:var(--card);border:1px solid var(--rule);border-radius:4px;padding:12px 14px 10px}
figcaption{display:flex;align-items:baseline;justify-content:space-between;margin-bottom:8px}
.par{font-family:var(--mono);font-weight:600;font-size:14px;letter-spacing:.02em}
.val{font-family:var(--mono);font-weight:600;font-size:22px;letter-spacing:-.02em}
.spark{width:100%%;height:78px;display:block}
.spark polyline{fill:none;stroke:#2E5B8C;stroke-width:1.1;vector-effect:non-scaling-stroke;
  stroke-linejoin:round;stroke-linecap:round}
.zona-alta{fill:#9A3B3B;opacity:.07}
.zona-baja{fill:#2E5B8C;opacity:.07}
.mediana{stroke:#C8D0D9;stroke-width:1;stroke-dasharray:3 3;vector-effect:non-scaling-stroke}
.pie{display:flex;justify-content:space-between;font-family:var(--mono);font-size:10.5px;
  color:var(--mute);margin-top:6px}
.nodata{height:78px;display:flex;align-items:center;justify-content:center;
  font-size:12px;color:var(--mute)}
footer{border-top:1px solid var(--ink);margin-top:clamp(28px,4vw,48px);
  padding-block:28px 56px}
footer p{max-width:78ch;font-size:13.5px;line-height:1.6;color:var(--mute)}
footer p+p{margin-top:.8em}
footer b{color:var(--body)}
code{font-family:var(--mono);font-size:.92em;background:var(--paper);
  border:1px solid var(--rule);border-radius:2px;padding:0 4px}
@media (max-width:640px){.figs>div{border-right:0;border-bottom:1px solid var(--rule)}}
</style>
</head>
<body>
<div class="wrap">

<header>
  <span class="kicker">Estructura temporal del VIX</span>
  <h1>Percentil historico de cada par de la curva</h1>
  <p class="lede">Los 8 primeros vencimientos de futuros del VIX, cruzados entre si
  (28 pares). Para cada dia se calcula donde queda ese par dentro de <b>toda su
  historia previa</b>. Datos desde %(desde)s, %(n_dias)s sesiones.</p>
  <div class="barra">
    <a class="btn" href="data/vix_futuros_M1_M8.csv" download>Descargar CSV historico (M1-M8)</a>
    <a class="btn sec" href="data/vix_percentiles_pares.csv" download>CSV de percentiles (28 pares)</a>
  </div>
  <div class="figs">
    <div><span class="k">Ultimo dato</span><span class="n">%(fecha)s</span>
         <span class="s">curva: %(curva)s</span></div>
    <div><span class="k">Par mas alto hoy</span>
         <span class="n" style="color:var(--alto)">%(maxval)s</span>
         <span class="s">%(maxpar)s &mdash; lo mas cerca de backwardation</span></div>
    <div><span class="k">Par mas bajo hoy</span>
         <span class="n" style="color:var(--bajo)">%(minval)s</span>
         <span class="s">%(minpar)s &mdash; lo mas cerca de contango extremo</span></div>
  </div>
</header>

<hr>

<section>
  <h2>Los 28 pares</h2>
  <p class="sub">Cada panel es un par Mj/Mi. La linea recorre su percentil a lo largo
  del tiempo; el numero grande es el valor de hoy. <b>100 significa que la curva esta
  del reves</b> en ese tramo (el vencimiento corto mas caro que el largo, tipico de
  panico) y <b>0 significa contango extremo</b> (el largo mucho mas caro, tipico de
  calma). La franja roja marca el 5%% superior y la azul el 5%% inferior; la linea
  discontinua es la mediana.</p>
  <div class="leyenda">
    <b><span class="sw" style="background:#9A3B3B"></span>95 o mas: extremo alto</b>
    <b><span class="sw" style="background:#2E5B8C"></span>5 o menos: extremo bajo</b>
    <b><span class="sw" style="background:#5B6672"></span>zona normal</b>
  </div>
  <div class="grid">
%(paneles)s
  </div>
</section>

<footer>
  <p><b>Fuentes.</b> Precios de liquidacion (settlement) de los futuros VIX del Cboe
  Futures Exchange. De abril-2007 a agosto-2018, del archivo publico oficial del propio
  exchange, contrato a contrato. De septiembre-2018 en adelante, de TradingView, que
  publica el mismo precio de liquidacion (verificado: coincide al cuarto decimal con el
  settlement oficial en las fechas recientes que el exchange aun sirve).</p>
  <p><b>Metodo.</b> Para cada dia se ordenan los contratos vivos por vencimiento y se
  asignan a M1..M8 (M1 = el mas proximo a vencer; el contrato deja de ser M1 el mismo
  dia que liquida). El percentil de cada par es <i>expanding</i>: se compara contra toda
  la historia anterior a esa fecha, sin mirar al futuro. Los primeros %(min_hist)s dias
  de cada serie no se pintan porque un percentil contra tan pocas observaciones no
  significa nada. Se filtran los dias sin sesion usando el calendario del CFE.</p>
  <p><b>Aviso sobre 2007.</b> Hasta el 23 de marzo de 2007 los futuros del VIX cotizaban
  a diez veces el indice; el 26 de marzo el exchange los reescalo. Esta serie empieza
  despues de ese cambio para no mezclar las dos escalas.</p>
  <p>Generado el %(generado)s. Datos de mercado publicos, publicados sin garantia de
  exactitud y sin fin comercial. Esto no es asesoramiento de inversion.</p>
</footer>

</div>
</body>
</html>
"""


if __name__ == "__main__":
    main()
