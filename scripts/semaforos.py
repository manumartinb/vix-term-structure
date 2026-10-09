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
  regla_txt, accion_si, accion_no, salida, evidencia: textos de la tarjeta (ASCII). Pueden llevar
            {hora_es}: se sustituye por la hora de Espana que corresponde a las 10:30 de Nueva York
            en el dia de la entrada (16:30 casi todo el ano, 15:30 en las semanas en que EE. UU. y
            Europa cambian de hora en fechas distintas). Al ir por Telegram se escapan (&, <, >).
  aviso_vivo     None (sin aviso en vivo) | "previo" (aviso de que se esta encendiendo; la entrada
                 sigue siendo con el cierre) | "entrada" (operar en el momento: solo si esta PROBADO)
  confirmaciones lecturas en vivo SEGUIDAS en verde (cada 15 min, patas del momento) para avisar
  aviso_previo_txt  texto del aviso previo (que pasara si cierra asi)
  edge           (opcional) 'fuerte' | 'moderado': solidez de la evidencia; se ensena en la tarjeta y en los Telegram
                 (fuerte = aprueba tambien fuera de muestra; moderado = gana en desarrollo, no confirmada fuera).
  certificada    (opcional, defecto True) False = estrategia estudiada y NO certificada: la web la pinta
                 en GRIS como informativa (nunca 'VERDE', no cuenta para el DOBLE VERDE) y ningun Telegram
                 la menciona (ni el de las 15:15, ni el de la curva, ni el aviso en vivo: vivo.avisos_vivo la
                 salta aunque tenga aviso_vivo).
El aviso en vivo sale como mucho UNA vez al dia por semaforo (vivo.py --avisos-vivo, estado en
data/semaforos_vivo_estado.json) y no sale si el semaforo ya esta en verde con el cierre oficial.

TIEMPOS (importante para operar)
El estado oficial es el del ULTIMO CIERRE OFICIAL del CBOE, que la corrida de las 08:00 publica.
La accion es para la SESION SIGUIENTE a ese cierre. La lectura en vivo es PROVISIONAL: anticipa
como puede quedar el cierre de hoy, que es el que mandara manana.

HUECOS (auditoria 2026-10-08, A-H2): si el ULTIMO cierre de la serie no trae todas las patas del
semaforo, el estado es SIN DATO (con 'motivo'), NUNCA el del ultimo dia valido: antes se
republicaba el verde de ayer como si fuera de hoy.

Reglas tecnicas del proyecto: ASCII, cp1252.
"""

import html
import datetime as dt

import numpy as np
import pandas as pd

SEMAFOROS = [
    {
        "id": "long_put_uvxy",
        "apuesta": "UVXY a la baja",
        "edge": "fuerte",
        "nombre": "LONG PUT UVXY",
        "variable": "M2/M1 - 1",
        "patas": ("M1", "M2"),
        "valor": lambda c: c["M2"] / c["M1"] - 1.0,
        "regla": lambda x: x < 0.0,
        "formato": "pct",
        "regla_txt": ("Verde si en el cierre oficial el segundo futuro (M2) queda por debajo del "
                      "primero (M1): curva en backwardation, M2/M1 - 1 negativo."),
        "accion_si": ("Comprar puts de UVXY con mas de 200 dias a vencimiento, en los strikes con delta "
                      "mas cercana a -0,40 y a -0,50, a precio medio, a las 10:30 de Nueva York "
                      "({hora_es} en Espana) de la sesion siguiente al cierre. Cada sesion en verde es "
                      "una entrada. La evidencia promedia todos los vencimientos de mas de 200 dias y los "
                      "dos deltas; el vencimiento mas lejano rinde unos 3 pp menos."),
        "accion_no": "Sin entrada.",
        "salida": ("Vender cuando pase la mitad de los dias que le quedaban a la put al comprarla "
                   "(si quedaban 330, a los 165)."),
        "aviso_vivo": "previo",
        "confirmaciones": 2,
        "aviso_previo_txt": ("Si cierra asi, la entrada es en la sesion siguiente, a las 10:30 de Nueva York "
                             "({hora_es} en Espana): te la confirmo manana a las 15:15 hora de Espana, antes de la entrada. Hoy no compres: comprar el "
                             "mismo dia no mejora (medido: -0,7 pp el primer dia del episodio)."),
        "evidencia": ("Backtest a precio medio: +17 % por operacion en 2019-2025 y +18 % fuera de "
                      "muestra en 2017-2018. Veredicto GO CONDICIONAL: riesgo de episodios como "
                      "enero de 2020 (primer backwardation de una crisis que luego se agrava)."),
    },
    {
        "id": "short_call_uvxy",
        "apuesta": "UVXY a la baja",
        "edge": "fuerte",
        "nombre": "SHORT CALL UVXY",
        "variable": "M2/M1 - 1",
        "patas": ("M1", "M2"),
        "valor": lambda c: c["M2"] / c["M1"] - 1.0,
        "regla": lambda x: x < 0.0,
        "formato": "pct",
        "regla_txt": ("Misma senal que la LONG PUT: verde si en el cierre oficial M2 queda por debajo de M1 (backwardation). "
                      "La senal original del Excel (percentil de jugosidad < 15) rinde lo mismo y entra menos dias."),
        "accion_si": ("Vender call spread de UVXY: vender la call con mas de 220 dias a vencimiento (deltas 0,3 a 0,6: con "
                      "0,1-0,2 casi nunca existe la pata de techo) y "
                      "comprar, del mismo vencimiento, la call del primer strike igual o mayor que el doble del vendido "
                      "(techo). Si esa call no cotiza, esa linea no se opera. Precio medio, a las 10:30 de Nueva York "
                      "({hora_es} en Espana) de la sesion siguiente al cierre. NUNCA la call desnuda: en mar-2020 la "
                      "cartera perdio mas del doble de su capital."),
        "accion_no": "Sin entrada.",
        "salida": ("Recomprar el spread cuando pase el 90 % de los dias que le quedaban al vender (si quedaban 380, a los "
                   "342). Sin stops: cerrar en el pico del miedo es lo peor que se puede hacer con esta venta."),
        "aviso_vivo": "previo",
        "confirmaciones": 2,
        "aviso_previo_txt": ("Si cierra asi, la entrada (vender el call spread 2x) es en la sesion siguiente, a las 10:30 de "
                             "Nueva York ({hora_es} en Espana): te la confirmo manana a las 15:15 hora de Espana, antes de la entrada. Hoy no vendas: vender el mismo dia "
                             "no se ha demostrado mejor el primer dia del episodio."),
        "evidencia": ("Backtest a precio medio (spread 2x, senal M2/M1<0, salida a 0,9 de la vida): +8,5 % por operacion "
                      "sobre la perdida maxima en 2019-2026 y +9,6 % fuera de muestra en 2017-2018; @APR GO. Misma apuesta "
                      "que la LONG PUT (UVXY a la baja)."),
    },
    {
        "id": "short_put_uvxy",
        "apuesta": "UVXY sin desplome",
        "edge": "moderado",
        "nombre": "SHORT PUT UVXY",
        "variable": "M2/M1 - 1",
        "patas": ("M1", "M2"),
        "valor": lambda c: c["M2"] / c["M1"] - 1.0,
        "regla": lambda x: x > 0.10845,
        "formato": "pct",
        "regla_txt": ("Contango fuerte: en el cierre oficial M2 queda mas de un 10,85 % por encima de M1 (umbral fijo calibrado "
                      "en 2008-2016 para cubrir los mismos dias que la senal estudiada, percentil as-of de M2/M1 > 85). EDGE "
                      "MODERADO: gana en 2019-2025 (PF 4,5) pero no se confirmo en 2017-2018 (PF 0,76) y la senal sobre todo "
                      "recorta la cola. Habilitada por decision del usuario (9-oct-2026)."),
        "accion_si": ("Vender put de UVXY con 15 a 29 dias a vencimiento y delta 0,1-0,2 (strike en torno al 80 % de UVXY), a "
                      "pelo y con el efectivo para cubrirla (strike x 100), a precio medio, a las 10:30 de Nueva York "
                      "({hora_es} en Espana) de la sesion siguiente al cierre. Edge moderado: tamano pequeno. Si la put no "
                      "tiene comprador (bid 0), no se vende."),
        "accion_no": "Sin entrada.",
        "salida": ("Recomprar la put cuando pase el 90 % de los dias que le quedaban al vender (unos 19 dias). Sin stops."),
        "aviso_vivo": "previo",
        "confirmaciones": 2,
        "aviso_previo_txt": ("Si cierra asi, la entrada (vender la put) es en la sesion siguiente, a las 10:30 de Nueva York "
                             "({hora_es} en Espana): te la confirmo manana a las 15:15 hora de Espana, antes de la entrada. "
                             "Hoy no vendas: vender el mismo dia no mejora."),
        "evidencia": ("Precio medio, put a pelo cubierta con efectivo, salida a 0,9 de la vida: con la regla de esta tarjeta "
                      "+0,85 % por operacion en 2019-2025 (estas puts casi no cotizan desde sep-2025) y -0,15 % en 2017-2018 "
                      "(fuera de muestra), cuando sin senal daba +0,16 %. Con la senal estudiada: PF 4,5 en 2019-2025, 0,76 "
                      "en 2017-2018 y 2,8 contando 2017-2025 (83 % ganadoras). @APR NO-GO (la senal no gradua el retorno). "
                      "Lo que aguanta en las dos muestras: la cola (peor 5 % -6,0 % frente a -12,0 % sin senal). Nunca "
                      "coincide con la LONG PUT ni con la SHORT CALL (apuesta contraria)."),
    },
]

HIST_SESIONES = 260      # cuanto historial viaja a la web para la mini grafica
SEPARA_EPISODIOS = 7     # dias naturales sin verde que separan dos episodios
TEXTOS = ("regla_txt", "accion_si", "accion_no", "salida", "evidencia", "aviso_previo_txt")


# ------------------------------------------------------------------ horas
def _domingo_n(anio, mes, n):
    """n-esimo domingo del mes (n=-1: el ultimo)."""
    if n > 0:
        d = dt.date(anio, mes, 1)
        d += dt.timedelta(days=(6 - d.weekday()) % 7)
        return d + dt.timedelta(weeks=n - 1)
    d = dt.date(anio + (mes == 12), mes % 12 + 1, 1) - dt.timedelta(days=1)
    return d - dt.timedelta(days=(d.weekday() - 6) % 7)


def _hora_espana_manual(fecha, hh, mm):
    """Sin zoneinfo: EE. UU. en verano del 2o domingo de marzo al 1er domingo de noviembre; Europa del
    ultimo domingo de marzo al ultimo de octubre. Diferencia Madrid - Nueva York: 6 h salvo desfases."""
    a = fecha.year
    us = _domingo_n(a, 3, 2) <= fecha < _domingo_n(a, 11, 1)
    eu = _domingo_n(a, 3, -1) <= fecha < _domingo_n(a, 10, -1)
    dif = 6 + (1 if eu else 0) - (1 if us else 0)
    t = dt.datetime(a, fecha.month, fecha.day, hh, mm) + dt.timedelta(hours=dif)
    return t.strftime("%H:%M")


def hora_espana(fecha, hh=10, mm=30):
    """Hora de Madrid que corresponde a hh:mm de Nueva York el dia 'fecha'."""
    fecha = pd.Timestamp(fecha).date()
    try:
        from zoneinfo import ZoneInfo
        t = dt.datetime(fecha.year, fecha.month, fecha.day, hh, mm, tzinfo=ZoneInfo("America/New_York"))
        return t.astimezone(ZoneInfo("Europe/Madrid")).strftime("%H:%M")
    except Exception:
        return _hora_espana_manual(fecha, hh, mm)


def siguiente_sesion(fecha):
    """Siguiente dia de lunes a viernes (los festivos no cambian la hora de un dia a otro)."""
    d = pd.Timestamp(fecha).normalize() + pd.Timedelta(days=1)
    while d.weekday() >= 5:
        d += pd.Timedelta(days=1)
    return d


def textos(sem, fecha_accion):
    """Textos del semaforo con {hora_es} resuelto para el dia de la entrada."""
    h = hora_espana(fecha_accion)
    return dict((k, sem.get(k, "").replace("{hora_es}", h)) for k in TEXTOS)


def esc(x):
    """Para Telegram (parse_mode HTML): escapa &, < y > de los textos del registro."""
    return html.escape("" if x is None else str(x), quote=False)


# ------------------------------------------------------------------ evaluacion
def _serie_valor(sem, serie):
    cols = list(sem["patas"])
    sub = serie[cols].astype(float)
    return sem["valor"](sub).replace([np.inf, -np.inf], np.nan).dropna()


def evaluar(sem, serie):
    """Estado OFICIAL de un semaforo sobre la serie diaria de cierres (DataFrame M1..M8)."""
    base = {"id": sem["id"], "nombre": sem["nombre"], "variable": sem["variable"], "formato": sem["formato"],
            "apuesta": sem.get("apuesta"), "certificada": bool(sem.get("certificada", True)), "edge": sem.get("edge")}
    if serie is None or len(serie) == 0:
        base.update({"estado": "SIN DATO", "motivo": "No hay serie de cierres oficiales.", "valor": None})
        base.update(textos(sem, pd.Timestamp(dt.date.today())))
        return base
    ultimo = pd.Timestamp(serie.index[-1])
    f_acc = siguiente_sesion(ultimo)
    base.update(textos(sem, f_acc))
    base.update({"fecha_cierre": ultimo.strftime("%Y-%m-%d"), "fecha_accion": f_acc.strftime("%Y-%m-%d"),
                 "hora_entrada_es": hora_espana(f_acc)})
    v = _serie_valor(sem, serie)
    if v.empty:
        base.update({"estado": "SIN DATO", "valor": None,
                     "motivo": "La serie no trae %s validos en ningun cierre." % " y ".join(sem["patas"])})
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
        "valor": round(float(v.iloc[-1]), 6),
        "fecha_valor": f.strftime("%Y-%m-%d"),
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
    if pd.Timestamp(f) < ultimo:
        # el ultimo cierre no trae las patas: NUNCA se reutiliza el estado del ultimo dia valido
        base.update({"estado": "SIN DATO", "valor": None,
                     "motivo": ("El cierre oficial del %s no trae %s validos; el ultimo dato valido es del %s "
                                "y NO se reutiliza." % (ultimo.strftime("%d/%m/%Y"), " y ".join(sem["patas"]),
                                                        pd.Timestamp(f).strftime("%d/%m/%Y")))})
    return base


def evaluar_todos(serie):
    return [evaluar(s, serie) for s in SEMAFOROS]


def historias(serie):
    """Historia COMPLETA de cada semaforo (para la seccion 'Historia de las senales' de la web)."""
    out = []
    for s in SEMAFOROS:
        v = _serie_valor(s, serie)
        out.append({"id": s["id"], "nombre": s["nombre"], "variable": s["variable"], "formato": s["formato"],
                    "f": [d.strftime("%Y-%m-%d") for d in v.index], "v": [round(float(x), 5) for x in v.values],
                    "on": [1 if s["regla"](x) else 0 for x in v.values], "certificada": bool(s.get("certificada", True))})
    return out


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
        if r.get("motivo"):
            print("   motivo: " + r["motivo"])
        print("   entrada %s a las %s de Espana" % (r.get("fecha_accion"), r.get("hora_entrada_es")))
