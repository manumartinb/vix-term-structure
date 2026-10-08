"""
semaforos.py -- SEMAFOROS DE ENTRADA sobre la curva de futuros del VIX.

QUE ES
Un registro UNICO de reglas operativas (una por estrategia) que leen tres sitios:
  - construir_sitio.py  -> data.json["semaforos"]  -> tarjetas de la web (cierre OFICIAL)
  - vivo.py             -> vivo.json["semaforos"]  -> linea en vivo de la web y del Telegram de las 17:00
  - aviso_semaforos.py  -> Telegram de LUZ VERDE (L-V 15:15 Madrid, solo si alguno esta en verde)
La web pinta las tarjetas de forma generica: ANADIR UN SEMAFORO = anadir una entrada a
SEMAFOROS (con su funcion 'valor' y su 'regla'). No hay que tocar ni la web ni los avisos.

CONTRATO DE CADA ENTRADA
  id        identificador corto (ASCII, sin espacios): ancla de la tarjeta en la web
  nombre    lo que se lee en la tarjeta y en el Telegram
  variable  nombre de la magnitud que se mide (se ensena junto a su valor)
  patas     huecos de la curva que necesita (en vivo, si alguno no es del momento -> no fiable)
  valor     f(curva) -> numero. 'curva' admite un DataFrame (historia, vectorizado) o un
            diccionario {"M1": x, ...} (foto en vivo): solo aritmetica sobre columnas.
  regla     f(numero) -> True (verde) / False
  formato   "pct" (se ensena x100 con %) o "num"
  regla_txt, accion_si, accion_no, salida, evidencia: textos de la tarjeta (ASCII, sin '<' ni '>',
            porque viajan tambien por Telegram en parse_mode HTML)

TIEMPOS (importante para operar)
El estado oficial es el del ULTIMO CIERRE OFICIAL del CBOE, que la corrida de las 08:00 publica.
La accion es para la SESION SIGUIENTE a ese cierre. La lectura en vivo es PROVISIONAL: anticipa
como puede quedar el cierre de hoy, que es el que mandara manana.

Reglas tecnicas del proyecto: ASCII, cp1252.
"""

import numpy as np
import pandas as pd

SEMAFOROS = [
    {
        "id": "long_put_uvxy",
        "nombre": "LONG PUT UVXY",
        "variable": "M2/M1 - 1",
        "patas": ("M1", "M2"),
        "valor": lambda c: c["M2"] / c["M1"] - 1.0,
        "regla": lambda x: x < 0.0,
        "formato": "pct",
        "regla_txt": ("Verde si en el cierre oficial el segundo futuro (M2) queda por debajo del "
                      "primero (M1): curva en backwardation, M2/M1 - 1 negativo."),
        "accion_si": ("Comprar put de UVXY con mas de 200 dias a vencimiento y delta entre -0,40 y "
                      "-0,50, a precio medio, a las 10:30 de Nueva York (16:30 en Espana) de la "
                      "sesion siguiente al cierre. Cada sesion en verde es una entrada."),
        "accion_no": "Sin entrada.",
        "salida": ("Vender cuando pase la mitad de los dias que le quedaban a la put al comprarla "
                   "(si quedaban 330, a los 165)."),
        "evidencia": ("Backtest a precio medio: +17 % por operacion en 2019-2025 y +18 % fuera de "
                      "muestra en 2017-2018. Veredicto GO CONDICIONAL: riesgo de episodios como "
                      "enero de 2020 (primer backwardation de una crisis que luego se agrava)."),
    },
]

HIST_SESIONES = 260      # cuanto historial viaja a la web para la mini grafica
SEPARA_EPISODIOS = 7     # dias naturales sin verde que separan dos episodios


def _serie_valor(sem, serie):
    cols = list(sem["patas"])
    sub = serie[cols].astype(float)
    return sem["valor"](sub).replace([np.inf, -np.inf], np.nan).dropna()


def evaluar(sem, serie):
    """Estado OFICIAL de un semaforo sobre la serie diaria de cierres (DataFrame M1..M8)."""
    base = {"id": sem["id"], "nombre": sem["nombre"], "variable": sem["variable"],
            "formato": sem["formato"], "regla_txt": sem["regla_txt"], "accion_si": sem["accion_si"],
            "accion_no": sem["accion_no"], "salida": sem["salida"], "evidencia": sem["evidencia"]}
    v = _serie_valor(sem, serie)
    if v.empty:
        base.update({"estado": "SIN DATO"})
        return base
    on = v.map(lambda x: bool(sem["regla"](x)))
    f = v.index[-1]
    grupo = on.ne(on.shift()).cumsum()
    en_racha = grupo == grupo.iloc[-1]
    u12 = on[on.index > f - pd.Timedelta(days=365)]
    dias_si = u12[u12].index
    episodios = int((pd.Series(dias_si).diff().dt.days > SEPARA_EPISODIOS).sum() + 1) if len(dias_si) else 0
    h = v.iloc[-HIST_SESIONES:]
    base.update({
        "estado": "SI" if on.iloc[-1] else "NO",
        "fecha_cierre": f.strftime("%Y-%m-%d"),
        "valor": round(float(v.iloc[-1]), 6),
        "racha": int(en_racha.sum()),
        "desde": on.index[en_racha][0].strftime("%Y-%m-%d"),
        "ultima_si": on[on].index.max().strftime("%Y-%m-%d") if on.any() else None,
        "dias_si_12m": int(len(dias_si)),
        "episodios_12m": episodios,
        "sesiones_12m": int(len(u12)),
        "hist": {"f": [d.strftime("%Y-%m-%d") for d in h.index],
                 "v": [round(float(x), 5) for x in h.values],
                 "on": [1 if sem["regla"](x) else 0 for x in h.values]},
    })
    return base


def evaluar_todos(serie):
    return [evaluar(s, serie) for s in SEMAFOROS]


def evaluar_vivo(sem, curva, fiable):
    """Estado PROVISIONAL con la curva del instante. curva: {"M1": precio, ...};
    fiable: {"M1": True si es precio del momento, ...}."""
    try:
        if any(curva.get(p) is None for p in sem["patas"]):
            return {"id": sem["id"], "estado": "SIN DATO", "valor": None, "fiable": False}
        x = float(sem["valor"](dict((p, float(curva[p])) for p in sem["patas"])))
    except Exception:
        return {"id": sem["id"], "estado": "SIN DATO", "valor": None, "fiable": False}
    if not np.isfinite(x):
        return {"id": sem["id"], "estado": "SIN DATO", "valor": None, "fiable": False}
    return {"id": sem["id"], "estado": "SI" if sem["regla"](x) else "NO", "valor": round(x, 6),
            "fiable": all(bool(fiable.get(p)) for p in sem["patas"])}


def evaluar_vivo_todos(curva, fiable):
    return [evaluar_vivo(s, curva, fiable) for s in SEMAFOROS]


def txt_valor(fmt, x):
    """Texto del valor para consola y Telegram (coma decimal)."""
    if x is None:
        return "-"
    s = ("%+.2f %%" % (100.0 * x)) if fmt == "pct" else ("%+.4f" % x)
    return s.replace(".", ",")


def fecha_corta(iso):
    return "%s/%s" % (iso[8:10], iso[5:7]) if iso else "-"


if __name__ == "__main__":
    import percentiles_curva as pcv
    for r in evaluar_todos(pcv.cargar_serie()):
        print("%-16s %-8s cierre %s  %s = %s  | %d sesiones en %s desde %s | ultima SI %s | 12m: %d sesiones en SI, %d episodios"
              % (r["nombre"], r["estado"], r.get("fecha_cierre"), r["variable"],
                 txt_valor(r["formato"], r.get("valor")), r.get("racha", 0), r["estado"], r.get("desde"),
                 r.get("ultima_si"), r.get("dias_si_12m", 0), r.get("episodios_12m", 0)))
