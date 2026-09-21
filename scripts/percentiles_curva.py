"""
percentiles_curva.py -- los 28 cruces Mx/My de la curva de futuros VIX y su percentil
historico. Replica la logica de la pestana "Live" del VIX Studio, pero sobre la serie
propia de VIX_CURVE (ver descargar_futuros_vix.py).

QUE CALCULA
Para cada pareja (Mi, Mj) con j>i  -> ratio = Mj/Mi - 1   (28 parejas con 8 meses)
y su percentil contra la historia de ESA MISMA pareja.

CONVENCION DEL VIX STUDIO (se respeta para poder comparar):
  valor mostrado = 1 - PERCENTRANK.INC(historia_de_la_pareja, ratio_de_hoy)
  con recorte: si el ratio < min(historia) -> 0 ; si > max(historia) -> 1 (antes de invertir)
  Por eso CERCA DE 100 = backwardation / curva del reves (el corto caro respecto al largo)
  y CERCA DE 0 = contango pronunciado.
  Comprobado en las formulas del .xlsx: Live!C3 = 1-PERCENTRANK.INC(Inclinacion!V:V, L3/$K$3-1).

PERCENTRANK.INC de Excel = (nº de valores estrictamente menores) / (n - 1).

USO
  python percentiles_curva.py                 # triangulo de hoy contra toda la historia
  python percentiles_curva.py --fecha 2025-04-08
  python percentiles_curva.py --corte 2025-04-08   # historia truncada a esa fecha
  python percentiles_curva.py --ventana 1250       # solo los ultimos N dias habiles (~5 anios)

Reglas tecnicas del proyecto: ASCII, cp1252.
"""

import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
SERIE = os.path.join(HERE, "data", "vix_futuros_M1_M8.csv")

N_MESES = 8
PAREJAS = [(i, j) for i in range(1, N_MESES + 1) for j in range(i + 1, N_MESES + 1)]


def cargar_serie(path=SERIE):
    df = pd.read_csv(path, parse_dates=["Fecha"]).sort_values("Fecha")
    cols = ["M%d" % i for i in range(1, N_MESES + 1)]
    return df.set_index("Fecha")[cols]


def ratios(serie):
    """DataFrame con las 28 columnas 'M{j}/M{i}' = Mj/Mi - 1."""
    out = {}
    for i, j in PAREJAS:
        out["M%d/M%d" % (j, i)] = serie["M%d" % j] / serie["M%d" % i] - 1.0
    return pd.DataFrame(out, index=serie.index)


def percentrank_inc(hist, x):
    """PERCENTRANK.INC de Excel, con el recorte del VIX Studio. Devuelve 0..1."""
    h = np.asarray(hist, dtype=float)
    h = h[~np.isnan(h)]
    if len(h) < 2 or np.isnan(x):
        return np.nan
    if x < h.min():
        return 0.0
    if x > h.max():
        return 1.0
    return float((h < x).sum()) / (len(h) - 1)


def triangulo(serie, fecha=None, corte=None, ventana=None):
    """Percentiles (ya invertidos, 0-100) de las 28 parejas en 'fecha'.

    corte   : la historia de referencia se trunca en esa fecha (para simular la hoja
              congelada en abr-2025).
    ventana : usar solo los ultimos N dias habiles de historia (regimen reciente).
    """
    r = ratios(serie)
    if fecha is None:
        completos = r.dropna()
        if len(completos) == 0:
            raise SystemExit("No hay ninguna fecha con las 28 parejas completas.")
        fecha = completos.index[-1]
    fecha = pd.Timestamp(fecha)
    if fecha not in r.index:
        raise SystemExit("La fecha %s no esta en la serie." % fecha.date())

    hoy = r.loc[fecha]
    hist = r[r.index < fecha]
    if corte is not None:
        hist = hist[hist.index <= pd.Timestamp(corte)]
    if ventana is not None:
        hist = hist.tail(int(ventana))

    res = {}
    for col in r.columns:
        p = percentrank_inc(hist[col].values, hoy[col])
        res[col] = np.nan if np.isnan(p) else (1.0 - p) * 100.0
    return fecha, pd.Series(res), len(hist), hoy


def pintar(pcts, titulo=""):
    """Triangulo en texto monoespaciado, listo para un mensaje de Telegram."""
    lineas = []
    if titulo:
        lineas.append(titulo)
    lineas.append("     " + "".join("%4s" % ("M%d" % j) for j in range(2, N_MESES + 1)))
    for i in range(1, N_MESES):
        fila = "M%-3d " % i
        for j in range(2, N_MESES + 1):
            if j <= i:
                fila += "    "
            else:
                v = pcts.get("M%d/M%d" % (j, i), np.nan)
                fila += "   ." if np.isnan(v) else "%4d" % round(v)
        lineas.append(fila)
    return "\n".join(lineas)


def main():
    args = sys.argv[1:]

    def opt(nombre, cast=str):
        if nombre in args:
            return cast(args[args.index(nombre) + 1])
        return None

    fecha = opt("--fecha")
    corte = opt("--corte")
    ventana = opt("--ventana", int)

    serie = cargar_serie()
    f, pcts, n_hist, curva = triangulo(serie, fecha=fecha, corte=corte, ventana=ventana)

    print("Fecha            : %s" % f.date())
    print("Historia usada   : %d dias%s%s"
          % (n_hist,
             ("  (truncada en %s)" % corte) if corte else "",
             ("  (ventana %s)" % ventana) if ventana else ""))
    print("Curva (M1..M8)   : %s"
          % "  ".join("%.4g" % v for v in serie.loc[f].values))
    print()
    print(pintar(pcts, "Percentil por pareja (100 = backwardation extrema, 0 = contango extremo)"))
    print()
    v = pcts.dropna()
    print("MAX (U2 en la hoja): %5.1f   en %s" % (v.max(), v.idxmax()))
    print("MIN (U3 en la hoja): %5.1f   en %s" % (v.min(), v.idxmin()))


if __name__ == "__main__":
    main()
