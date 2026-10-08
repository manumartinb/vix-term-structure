"""
jugosidad_oficial.py -- MEDIDA UNICA de la "jugosidad" de la curva de futuros del VIX y de su percentil
as-of. La importan a la vez el backtest de la SHORT CALL (ESTRATEGIAS/UVXY/ANALISIS/SC_APR_STUDY) y la web
(semaforos.py), para que los dos usen EXACTAMENTE la misma vara. (Plan SHORT CALL, fase F0, 2026-10-08.)

DEFINICIONES (fijadas antes de mirar resultados)
  Fuente: serie OFICIAL de liquidaciones del Cboe, data/vix_futuros_M1_M8_detalle.csv (M1, M2 y los
  vencimientos reales VENC_M1..VENC_M8 de cada dia).
  CICLO(t)   = VENC_M1(t) menos el vencimiento mensual inmediatamente anterior, en dias naturales. El
               calendario son todos los vencimientos reales que aparecen en VENC_M1..VENC_M8. El dia del
               vencimiento la serie oficial ya ha pasado M1 al contrato siguiente: se usa ese ciclo nuevo.
  jug(t)     = (M2/M1 - 1) / (CICLO/2) * 21      (pendiente M2/M1 llevada a un mes de 21 sesiones)
  pct_asof(t)= 100 * (n.o de jug de dias ANTERIORES estrictamente menores que jug(t)) / (n.o de dias
               anteriores con jug valida). Exige al menos MIN_OBS dias anteriores validos; si no, NaN.
               Nunca entra el dia de hoy en su propia vara (sin mirar al futuro).
  Verde SHORT CALL: pct_asof < 15 (estricto), con el cierre del dia habil ANTERIOR a la entrada.

DIFERENCIAS CONOCIDAS con el rebuild antiguo (ANALISIS/VIX_LOW_EDGE_20260831/uvxy_rebuild_jugosidad.py):
el rebuild usaba un incrementoT hibrido (fichero original con errores hasta 2025-04-18 y calendario
teorico despues), una serie con festivos arrastrados y filas copiadas, y un expanding de pandas que
contaba el dia actual en la ventana. Esta medida los sustituye; el contraste esta en 41_sc_vara_oficial.py.

Reglas tecnicas del proyecto: ASCII, cp1252.
"""

import os
import bisect

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DETALLE = os.path.join(HERE, "data", "vix_futuros_M1_M8_detalle.csv")
MIN_OBS = 250
UMBRAL_SC = 15.0
VENC_COLS = ["VENC_M%d" % k for k in range(1, 9)]


def cargar_detalle(path=None):
    d = pd.read_csv(path or DETALLE, parse_dates=["Fecha"])
    for c in VENC_COLS:
        if c in d.columns:
            d[c] = pd.to_datetime(d[c], errors="coerce")
    return d.sort_values("Fecha").reset_index(drop=True)


def calendario_vencimientos(detalle):
    """Todos los vencimientos reales que aparecen en VENC_M1..VENC_M8, ordenados."""
    cols = [c for c in VENC_COLS if c in detalle.columns]
    v = pd.to_datetime(pd.Series(detalle[cols].values.ravel()), errors="coerce").dropna().unique()
    return pd.DatetimeIndex(sorted(v))


def ciclo_de(venc_m1, calendario):
    """Dias entre venc_m1 y el vencimiento anterior del calendario (NaN si no hay anterior)."""
    if venc_m1 is None or pd.isna(venc_m1):
        return np.nan
    venc_m1 = pd.Timestamp(venc_m1)
    previos = calendario[calendario < venc_m1]
    if len(previos) == 0:
        return np.nan
    return float((venc_m1 - previos[-1]).days)


def jugosidad(m1, m2, ciclo):
    """jug = (M2/M1 - 1) / (CICLO/2) * 21. Vale con escalares o con Series."""
    r = m2 / m1 - 1.0
    j = r / (ciclo / 2.0) * 21.0
    if isinstance(j, pd.Series):
        return j.where((m1 > 0) & (m2 > 0) & (ciclo > 0))
    try:
        ok = m1 > 0 and m2 > 0 and ciclo > 0 and np.isfinite(j)
    except Exception:
        ok = False
    return float(j) if ok else np.nan


def percentil_asof(valores, min_obs=MIN_OBS):
    """Percentil de cada valor frente a los valores VALIDOS anteriores (estrictamente menores)."""
    out = np.full(len(valores), np.nan)
    hist = []
    for i, x in enumerate(valores):
        if x is None or not np.isfinite(x):
            continue
        if len(hist) >= min_obs:
            out[i] = 100.0 * bisect.bisect_left(hist, x) / len(hist)
        bisect.insort(hist, x)
    return out


def vara(valores):
    """Lista ordenada de los valores validos (la historia hasta el ultimo cierre)."""
    v = [float(x) for x in valores if x is not None and np.isfinite(x)]
    v.sort()
    return v


def consultar(vara_ordenada, x, min_obs=MIN_OBS):
    """Percentil de x (p. ej. la lectura en vivo) frente a la vara, sin meterlo en ella."""
    if x is None or not np.isfinite(x) or len(vara_ordenada) < min_obs:
        return np.nan
    return 100.0 * bisect.bisect_left(vara_ordenada, x) / len(vara_ordenada)


def serie(detalle=None):
    """Serie diaria oficial: dia, M1, M2, VENC_M1, ciclo, m2m1, jug, pct_asof, verde_sc."""
    d = detalle if detalle is not None else cargar_detalle()
    cal = calendario_vencimientos(d)
    calv = cal.values
    vm1 = pd.to_datetime(d["VENC_M1"]).values
    idx = np.searchsorted(calv, vm1, side="left")
    previo = np.array([calv[i - 1] if (i > 0 and not pd.isna(v)) else np.datetime64("NaT")
                       for i, v in zip(idx, vm1)], dtype="datetime64[ns]")
    ciclo = (vm1 - previo).astype("timedelta64[D]").astype(float)
    ciclo[pd.isna(vm1) | pd.isna(previo)] = np.nan
    out = pd.DataFrame({"dia": d["Fecha"].values, "M1": d["M1"].astype(float).values,
                        "M2": d["M2"].astype(float).values, "VENC_M1": vm1, "ciclo": ciclo})
    out["m2m1"] = (out["M2"] / out["M1"] - 1.0).where((out["M1"] > 0) & (out["M2"] > 0))
    out["jug"] = jugosidad(out["M1"], out["M2"], out["ciclo"])
    out["pct_asof"] = percentil_asof(out["jug"].values)
    out["verde_sc"] = out["pct_asof"] < UMBRAL_SC
    return out


if __name__ == "__main__":
    s = serie()
    u = s.dropna(subset=["pct_asof"]).iloc[-1]
    print("filas %d | primer percentil %s | ultimo cierre %s: M2/M1 %+.3f %%, ciclo %d d, jug %+.5f, pct %.1f -> %s"
          % (len(s), s.dropna(subset=["pct_asof"]).dia.iloc[0].date(), pd.Timestamp(u.dia).date(), 100 * u.m2m1,
             u.ciclo, u.jug, u.pct_asof, "VERDE" if u.verde_sc else "no"))
