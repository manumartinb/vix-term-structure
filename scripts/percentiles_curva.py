"""
percentiles_curva.py -- LIBRERIA COMPARTIDA: los 28 cruces Mx/My de la curva de
futuros VIX y su percentil historico.

ES LA UNICA FUENTE DEL CALCULO. La web (`construir_sitio.py`) y el aviso diario
(`radar_telegram.py`) llaman AQUI. Motivo: el 2026-09-21 se implemento el percentil
condicional solo en la web y el radar se quedo con el global; durante unas horas
publicaron numeros distintos para el mismo dia (7,1 puntos de media, 13,5 de maximo,
y 3.966 de 114.729 celdas cruzaban la frontera de alerta en una convencion y no en
la otra). Con el calculo en un solo sitio esa divergencia ya no es posible.

QUE CALCULA
Para cada pareja (Mi, Mj) con j>i  -> ratio = Mj/Mi - 1   (28 parejas con 8 meses)
y su percentil contra la historia de ESA MISMA pareja.

CONVENCION (heredada del VIX Studio, se respeta para poder comparar):
  valor mostrado = 100 - percentil  ->  CERCA DE 100 = backwardation / curva del
  reves;  CERCA DE 0 = contango pronunciado.
  (La convexidad es la excepcion: va SIN invertir, como en su hoja.)

PERCENTIL CONDICIONAL POR DIAS A VENCIMIENTO
Cada dia se compara SOLO contra dias anteriores que estaban a una distancia
parecida del vencimiento del front month (DTE +/- VENTANA_DTE). Sin esto el
percentil medía el calendario y no el mercado: como M1 converge al contado segun
vence, la base promediaba percentil 27 a 3 dias del roll y 61 a 30 dias (33,9
puntos de sesgo mecanico) y los pares con M1 arrastraban 13,0. Con la correccion
el sesgo real medido baja a 5,7 y 3,5.

USO
  python percentiles_curva.py                 # triangulo de hoy
  python percentiles_curva.py --fecha 2020-03-16
  python percentiles_curva.py --corte 2025-04-08   # historia truncada a esa fecha

Reglas tecnicas del proyecto: ASCII, cp1252.
"""

import os
import sys
import bisect

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
SERIE = os.path.join(HERE, "data", "vix_futuros_M1_M8.csv")
DETALLE = os.path.join(HERE, "data", "vix_futuros_M1_M8_detalle.csv")

N_MESES = 8
PAREJAS = [(i, j) for i in range(1, N_MESES + 1) for j in range(i + 1, N_MESES + 1)]

# --- parametros del percentil condicional (medidos, no elegidos a ojo) ---
VENTANA_DTE = 2      # +/-2 dias. Medido: +/-1 da 0,30 y +/-2 da 0,43 de sesgo
                     # residual; +/-3 se va a 5,69 y +/-5 a 16,83.
CAP_DTE = 30         # todo lo de >30 dias en un solo tramo. OBLIGATORIO: hay 153
                     # valores distintos de DTE y 128 con menos de 50 casos; sin
                     # acotar la serie no arrancaria hasta oct-2019 (-3.033 dias).
MIN_OBS_TRAMO = 100  # observaciones previas comparables antes de publicar


def cargar_serie(path=SERIE):
    df = pd.read_csv(path, parse_dates=["Fecha"]).sort_values("Fecha")
    cols = ["M%d" % i for i in range(1, N_MESES + 1)]
    return df.set_index("Fecha")[cols]


def cargar_dte(indice, path=DETALLE):
    """Dias hasta el vencimiento del front month, alineados al indice dado.

    Es la variable del ciclo para TODAS las familias: todos los contratos ruedan
    el mismo dia."""
    det = pd.read_csv(path, parse_dates=["Fecha", "VENC_M1"])
    dte = (det["VENC_M1"] - det["Fecha"]).dt.days
    dte.index = pd.DatetimeIndex(det["Fecha"])
    return dte.reindex(indice).values.astype(float)


def ratios(serie):
    """DataFrame con las 28 columnas 'M{j}/M{i}' = Mj/Mi - 1."""
    out = {}
    for i, j in PAREJAS:
        out["M%d/M%d" % (j, i)] = serie["M%d" % j] / serie["M%d" % i] - 1.0
    return pd.DataFrame(out, index=serie.index)


def percentil_condicional(valores, dte, invertir=True):
    """Percentil expanding CONDICIONADO a los dias que quedan para el vencimiento.

    Para el dia t se compara su valor SOLO contra los dias ANTERIORES que estaban
    a una distancia parecida del vencimiento (DTE +/- VENTANA_DTE). Asi un dato de
    "quedan 2 dias" se juzga contra otros dias-2, no contra dias-30, que es lo que
    hacia que el panel marcase extremos falsos en cada roll.

    Implementacion: una lista ordenada por DTE exacto; la consulta suma los
    conteos de las 2*VENTANA+1 listas de la ventana. Exacto y O(n log n).

    invertir=True aplica el 100-p de la convencion del VIX Studio. La convexidad
    va con invertir=False, como en su hoja."""
    listas = {}
    out = np.full(len(valores), np.nan)
    for i, (v, t) in enumerate(zip(valores, dte)):
        if v is None or (isinstance(v, float) and np.isnan(v)) or np.isnan(t):
            continue
        b = int(min(t, CAP_DTE))
        menores = total = 0
        for k in range(b - VENTANA_DTE, b + VENTANA_DTE + 1):
            L = listas.get(k)
            if L:
                menores += bisect.bisect_left(L, v)
                total += len(L)
        if total >= MIN_OBS_TRAMO:
            p = min(1.0, max(0.0, menores / float(total - 1)))
            out[i] = (1.0 - p) * 100.0 if invertir else p * 100.0
        listas.setdefault(b, [])
        bisect.insort(listas[b], v)
    return out


def matriz_percentiles(crudos, dte=None, invertir=True):
    """Percentil condicional de TODAS las columnas de un DataFrame de valores
    crudos. Es el punto de entrada que usan la web y el radar: un solo camino."""
    if dte is None:
        dte = cargar_dte(crudos.index)
    out = pd.DataFrame(index=crudos.index)
    for col in crudos.columns:
        out[col] = percentil_condicional(crudos[col].values, dte, invertir=invertir)
    return out


def triangulo(serie, fecha=None, corte=None, dte=None):
    """Percentiles (0-100, convencion invertida) de las 28 parejas en 'fecha'.

    corte: la historia de referencia se trunca en esa fecha (para simular una
    vara congelada). Devuelve (fecha, Series, n_dias_historia, ratios_de_ese_dia).
    """
    r = ratios(serie)
    if corte is not None:
        pass  # el corte se aplica abajo, sobre el indice, no sobre el calculo
    pct = matriz_percentiles(r if corte is None else r[r.index <= pd.Timestamp(corte)],
                             dte=dte if corte is None else None)
    completos = pct.dropna()
    if fecha is None:
        if len(completos) == 0:
            raise SystemExit("No hay ninguna fecha con las 28 parejas completas.")
        fecha = completos.index[-1]
    fecha = pd.Timestamp(fecha)
    if fecha not in pct.index:
        raise SystemExit("La fecha %s no esta en la serie." % fecha.date())
    n_hist = int((pct.index < fecha).sum())
    return fecha, pct.loc[fecha], n_hist, r.loc[fecha]


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
                fila += "   ." if (v is None or np.isnan(v)) else "%4d" % round(v)
        lineas.append(fila)
    return "\n".join(lineas)


def main():
    args = sys.argv[1:]

    def opt(nombre):
        if nombre in args:
            return args[args.index(nombre) + 1]
        return None

    serie = cargar_serie()
    f, pcts, n_hist, _ = triangulo(serie, fecha=opt("--fecha"), corte=opt("--corte"))

    print("Fecha            : %s" % f.date())
    print("Historia usada   : %d dias previos%s"
          % (n_hist, ("  (truncada en %s)" % opt("--corte")) if opt("--corte") else ""))
    print("Curva (M1..M8)   : %s"
          % "  ".join("%.4g" % v for v in serie.loc[f].dropna().values))
    print()
    print(pintar(pcts, "Percentil por pareja (100 = backwardation, 0 = contango)"))
    print()
    v = pcts.dropna()
    if len(v):
        print("MAX (U2 en la hoja): %5.1f   en %s" % (v.max(), v.idxmax()))
        print("MIN (U3 en la hoja): %5.1f   en %s" % (v.min(), v.idxmin()))


if __name__ == "__main__":
    main()
