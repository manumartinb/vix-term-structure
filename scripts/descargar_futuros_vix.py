"""
descargar_futuros_vix.py -- construye la serie historica M1..M8 de futuros VIX.

QUE HACE
Descarga el historico de CADA contrato mensual de futuros VIX y, para cada dia de
negociacion, ordena los contratos vivos por fecha de vencimiento y los reparte en
columnas M1..M8 (M1 = el mas cercano a vencer).

VENTANA: desde 2007-04-01 hasta hoy (decision del usuario, 2026-09-21). Lo anterior
se descarta: evita el reescalado del 2007-03-26 (ver mas abajo) y los anios en que
CFE aun no listaba meses suficientes para formar una curva de 8.

FUENTES -- SOLO LIQUIDACION OFICIAL DEL CBOE (desde el 2026-09-23)
  1) Archivo CDN antiguo:          contratos K04 (may-2004) .. Q18 (ago-2018)
     https://cdn.cboe.com/resources/futures/archive/volume-and-price/CFE_<CODE><YY>_VX.csv
     Columnas: Trade Date, Futures, Open, High, Low, Close, Settle, Change,
               Total Volume, EFP, Open Interest
     Verificado 2026-09-21: K04..Q18 responden 200; U18 en adelante dan 403.
  2) Fichero oficial por contrato ("historical data"): contratos que vencen desde 2013
     https://cdn.cboe.com/data/us/futures/market_statistics/historical_data/VX/VX_<VENC>.csv
     <VENC> = fecha de VENCIMIENTO (AAAA-MM-DD). Mismas columnas. Antes de 2013 -> 403.
     Se usa para todo lo que el archivo antiguo no tiene: los contratos de sep-2018
     en adelante, los 16 de sep-dic 2014-2017 que el antiguo no sirve, y la cola de
     los de mar-ago 2018, que el antiguo corta el 2018-02-23.
     Donde los dos se solapan (6.909 precios de 2013-2018) coinciden al cuarto decimal.
     El CBOE lo actualiza de madrugada: medido el 2026-09-23, Last-Modified 05:06 GMT
     (07:06 de Madrid) con la sesion del dia anterior. Por eso la corrida es de manana.

  Hasta el 2026-09-23 el tramo 2018 -> hoy venia de TradingView (tvDatafeed), y
  estaba MAL FECHADO: tvDatafeed pone la hora LOCAL de la maquina y la vela diaria de
  un futuro lleva la hora de APERTURA de su sesion (17:00 de Chicago del dia
  anterior). Con EEUU y Europa desincronizados (~3 semanas al ano, marzo y final de
  octubre) cada dia guardaba la liquidacion del dia SIGUIENTE (1.039 precios, 174
  dias), y la vela del dia siguiente a un festivo de EEUU caia en el festivo y la
  tiraba el filtro de dias fantasma (47 dias de mercado perdidos). Estudio completo:
  ESTRATEGIAS/ANALISIS/SETTLE_OFICIAL_VIX_20260923. TradingView queda SOLO para la
  banda en vivo (vivo.py, velas de 5 minutos), que no se guarda en la historia.

TRAMPA DE ESCALA (verificada 2026-09-21, critica)
Hasta el 2007-03-23 los futuros VIX cotizaban a DIEZ VECES el indice; el 2007-03-26
CFE los reescalo. Medido en el contrato J07: 03/23/2007 close=133.50 -> 03/26/2007
close=13.40. Este script divide entre 10 toda fila anterior a 2007-03-26.
Sin esa correccion, los ~3 primeros anios entran inflados: no afecta a los ratios
Mx/My (la escala se cancela) pero destroza los percentiles de nivel absoluto y los
de convexidad. La pestana Inclinacion del VIX Studio empieza en nov-2007 y por eso
nunca se topo con esto.

VENCIMIENTO
Regla CFE: miercoles 30 dias antes del tercer viernes del mes SIGUIENTE al del
contrato. Manda la fecha OFICIAL cuando se tiene: el fichero "historical data" se
llama por su vencimiento, asi que la fecha con la que responde ES el vencimiento.
Para los contratos del archivo antiguo se usa la ultima fecha de su propio fichero,
que incluye el dia de liquidacion. (Antes se usaba la ultima fecha observada en
TradingView, que no imprimia el dia de liquidacion en marzo: los 9 marzos de
2018-2026 quedaban un dia antes.) El script compara regla y fecha efectiva y
reporta cuantas veces difieren (control de sanidad).

SALIDAS (carpeta data/)
  contratos/<CONTRATO>.csv          cache crudo por contrato (no se re-descarga)
  vix_contratos_largo.csv           formato largo: fecha, contrato, vencimiento, close,
                                    settle, fuente, volumen, oi (volumen e interes
                                    abierto: vacios en los contratos viejos cacheados)
  vix_futuros_M1_M8.csv             pivotado limpio: Fecha, M1..M8
  vix_futuros_M1_M8_detalle.csv     idem + vencimiento de cada mes + n contratos vivos

CACHE: un contrato vencido se lee de la cache siempre que su fuente sea OFICIAL. Una
cache de otra fuente (TradingView, de antes del 2026-09-23) se descarta y se vuelve a
bajar del CBOE: la primera corrida tras el cambio migra sola.

USO
  python descargar_futuros_vix.py                 # usa cache, descarga solo lo que falte
  python descargar_futuros_vix.py --refresh       # re-descarga todo (ignora cache)
  python descargar_futuros_vix.py --limite 20     # solo los N primeros contratos (prueba rapida)
  python descargar_futuros_vix.py --dry-run       # no escribe nada, solo dice que haria
  (--solo-cboe se acepta por compatibilidad y ya no hace nada: todo es CBOE)

Reglas tecnicas del proyecto: ASCII, cp1252.
"""

import os
import sys
import time
import datetime as dt

import numpy as np
import pandas as pd

import estado

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
CACHE = os.path.join(DATA, "contratos")

CBOE_URL = ("https://cdn.cboe.com/resources/futures/archive/"
            "volume-and-price/CFE_%s%02d_VX.csv")
# fichero oficial por contrato, llamado por su fecha de VENCIMIENTO (2013 en adelante)
CBOE_HIST_URL = ("https://cdn.cboe.com/data/us/futures/market_statistics/"
                 "historical_data/VX/VX_%s.csv")
# Unicas fuentes admitidas en la historia. Una cache con otra etiqueta (TradingView)
# se descarta y se vuelve a bajar: asi la primera corrida tras el cambio migra sola.
FUENTES_OFICIALES = ("CBOE", "CBOE_HIST", "CBOE+HIST")
# Si la fecha de la regla no responde, se prueban estas desviaciones en dias. La
# regla ya contempla festivos; esto es red por si el calendario se equivoca.
DESVIOS_VENC = (0, 1, -1, 2, -2)

# codigo de mes de futuros: 1=enero ... 12=diciembre
CODIGOS = ["F", "G", "H", "J", "K", "M", "N", "Q", "U", "V", "X", "Z"]

# ventana de la serie: el usuario descarta lo anterior a abril-2007 (decision 2026-09-21).
# Empezar el 2007-04-01 deja fuera por completo el reescalado del 2007-03-26 y arranca
# donde CFE ya listaba suficientes meses seguidos.
FECHA_INICIO = pd.Timestamp("2007-04-01")

# primer y ultimo contrato a intentar (J07 = primer contrato que vence tras FECHA_INICIO)
PRIMER_ANIO, PRIMER_MES = 2007, 4
# El horizonte NO se clava a una fecha: se deriva del calendario en cada corrida.
# Estuvo fijo en 2027-07 y eso tiene dos caras, las dos malas: hoy sobran dos
# contratos que el CFE aun no lista (y "fallan" en cada corrida), y a partir de
# sep-2027 se habria quedado CORTO, perdiendo el tramo largo de la curva sin que
# nadie se enterara. Una fecha fija dentro de un sistema que rueda se pudre sola.
MARGEN_MESES = 2       # se intentan 2 vencimientos mas alla de M8, por adelantado
TOL_COLA = 5           # dias: si un contrato VENCIDO acaba antes de esto de su
                       # vencimiento, su fichero esta truncado y se completa

# frontera medida el 2026-09-21: el archivo CDN de CBOE llega hasta Q18 (ago-2018)
CBOE_HASTA = (2018, 8)

N_MESES = 8          # M1..M8
# CBOE limita por volumen: en la primera corrida masiva (170 peticiones seguidas)
# empezo a devolver 403 a partir del contrato ~2013 y el script cayo al plan B de
# TradingView sin que se notara. Con pausa + reintento la fuente oficial aguanta.
PAUSA_CBOE = 0.8
REINTENTOS_CBOE = 3

# reescalado CFE: antes de esta fecha el futuro cotizaba a 10x el indice
FECHA_REESCALADO = pd.Timestamp("2007-03-26")
FACTOR_PRE_REESCALADO = 10.0


# ---------------------------------------------------------------------------
# vencimientos
# ---------------------------------------------------------------------------
def tercer_viernes(anio, mes):
    d = dt.date(anio, mes, 1)
    offset = (4 - d.weekday()) % 7      # weekday(): lunes=0 ... viernes=4
    return d + dt.timedelta(days=offset + 14)


_HABILES_CFE = None


def _habiles_cfe():
    """Dias habiles del CFE, cacheados. Si el calendario no esta disponible se
    devuelve None y la regla se queda en su version sin ajustar: es mejor eso
    que inventarse festivos."""
    global _HABILES_CFE
    if _HABILES_CFE is None:
        try:
            import pandas_market_calendars as mcal
            dias = mcal.get_calendar("CFE").valid_days(
                start_date="2004-01-01", end_date="2030-12-31")
            _HABILES_CFE = set(pd.DatetimeIndex(dias).tz_localize(None).normalize())
        except Exception:
            _HABILES_CFE = False
    return _HABILES_CFE or None


def vencimiento_teorico(anio, mes):
    """Miercoles 30 dias antes del tercer viernes del mes siguiente (regla CFE),
    RETROCEDIDO al dia habil anterior si cae en festivo.

    El ajuste no es cosmetico: el 19-jun-2024 fue miercoles y Juneteenth, y el
    contrato VXM2024 vencio el 18. Sin retroceder, todas las sesiones de ese
    ciclo llevan el DTE corrido un dia, y desde el percentil condicional el DTE
    decide contra que tramo de la historia se compara cada dia."""
    if mes == 12:
        a2, m2 = anio + 1, 1
    else:
        a2, m2 = anio, mes + 1
    ref = tercer_viernes(a2, m2)
    hab = _habiles_cfe()
    if hab:
        # (1) si el VIERNES DE REFERENCIA es festivo, la referencia es el habil
        # anterior y los 30 dias se cuentan desde ahi. Este es el ajuste que de
        # verdad importa y el que faltaba: comprobado contra las fechas que
        # publica el propio CBOE, el M8 del 21-sep-2026 vence el 18-may-2027 y
        # no el 19, porque el tercer viernes de junio de 2027 es el 18 y ese dia
        # se observa Juneteenth (el 19 cae en sabado).
        for _ in range(7):
            if pd.Timestamp(ref) in hab:
                break
            ref = ref - dt.timedelta(days=1)
    v = ref - dt.timedelta(days=30)
    if hab:
        # (2) y si aun asi el resultado cae en festivo, al habil anterior.
        for _ in range(7):
            if pd.Timestamp(v) in hab:
                break
            v = v - dt.timedelta(days=1)
    return v


def proximo_vencimiento(fecha):
    """Primer vencimiento mensual de VX estrictamente posterior a 'fecha'."""
    f = pd.Timestamp(fecha)
    a, m = f.year, f.month
    for _ in range(3):
        v = pd.Timestamp(vencimiento_teorico(a, m))
        if v > f:
            return v
        m += 1
        if m > 12:
            a, m = a + 1, 1
    return pd.Timestamp(vencimiento_teorico(a, m))


def escalera_vencimientos(desde, hasta):
    """Todos los vencimientos mensuales TEORICOS del CFE en el rango, ordenados.

    Es la rejilla contra la que se asignan los huecos M1..M8. Se construye del
    CALENDARIO, no de los contratos descargados: por eso un contrato que falte
    deja su hueco vacio en vez de desplazar a los demas."""
    out = []
    a, m = desde.year, desde.month
    while (a, m) <= (hasta.year, hasta.month):
        out.append(pd.Timestamp(vencimiento_teorico(a, m)))
        m += 1
        if m > 12:
            a, m = a + 1, 1
    return pd.DatetimeIndex(sorted(out))


def anclar_a_escalera(vencimientos, escalera, tol_dias=6):
    """Ancla cada vencimiento REAL al peldano de calendario que le corresponde.

    Hace falta porque la regla teorica del CFE difiere del vencimiento observado
    en 1-3 dias en 16 de 264 contratos (festivos). Sin este anclaje, esos 16 se
    asignarian a un peldano equivocado."""
    esc = np.array(escalera.view("int64"))
    out = []
    for v in pd.DatetimeIndex(vencimientos).view("int64"):
        i = int(np.abs(esc - v).argmin())
        dif = abs(esc[i] - v) / 86400000000000.0
        out.append(escalera[i] if dif <= tol_dias else pd.NaT)
    return pd.DatetimeIndex(out)


def csv_atomico(df, ruta, **kw):
    """to_csv que no puede dejar un fichero a medias: se escribe al lado y se
    renombra. os.replace es atomico dentro del mismo volumen, tambien en Windows."""
    tmp = ruta + ".tmp"
    df.to_csv(tmp, **kw)
    os.replace(tmp, ruta)


def lista_contratos():
    """[(nombre, anio, mes, codigo, vencimiento_teorico), ...] en orden cronologico."""
    hoy = pd.Timestamp(dt.date.today())
    esc = escalera_vencimientos(hoy, hoy + pd.Timedelta(days=900))
    base = int(esc.searchsorted(hoy, side="right"))
    tope = esc[base + N_MESES - 1 + MARGEN_MESES]
    ua, um = tope.year, tope.month

    out = []
    a, m = PRIMER_ANIO, PRIMER_MES
    while (a, m) <= (ua, um):
        cod = CODIGOS[m - 1]
        out.append(("VX%s%04d" % (cod, a), a, m, cod, vencimiento_teorico(a, m)))
        m += 1
        if m > 12:
            a, m = a + 1, 1
    return out


# ---------------------------------------------------------------------------
# descarga
# ---------------------------------------------------------------------------
def _parsear_cboe(raw):
    """CSV del CBOE -> DataFrame [fecha, close, settle, volumen, oi] o None.

    Comun a las dos fuentes del exchange, que traen las mismas columnas."""
    # Los ficheros de 2013 en adelante llevan un PARRAFO de aviso legal ANTES de la
    # cabecera; los de 2007-2012 empiezan directamente en "Trade Date". Hay que buscar
    # la cabecera, no suponer que es la linea 1.
    # (Este detalle costo un diagnostico equivocado: parecia que CBOE limitaba por
    #  volumen y en realidad el script descartaba los ficheros con preambulo.)
    lineas = [ln.rstrip().rstrip(",") for ln in raw.splitlines() if ln.strip()]
    inicio = None
    for i, ln in enumerate(lineas):
        if ln.lower().startswith("trade date"):
            inicio = i
            break
    if inicio is None:
        return None
    lineas = lineas[inicio:]
    import io
    df = pd.read_csv(io.StringIO("\n".join(lineas)))
    df = df.rename(columns={"Trade Date": "fecha", "Close": "close", "Settle": "settle",
                            "Total Volume": "volumen", "Open Interest": "oi"})
    if "fecha" not in df.columns:
        return None
    df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce")
    for c in ("close", "settle", "volumen", "oi"):
        df[c] = pd.to_numeric(df[c], errors="coerce") if c in df.columns else np.nan
    df = df.dropna(subset=["fecha"])
    # filas con precio 0 = contrato listado pero sin negociar ese dia -> fuera
    df = df[(df["close"].fillna(0) > 0) | (df["settle"].fillna(0) > 0)]
    if len(df) == 0:
        return None
    # reescalado CFE del 2007-03-26: lo anterior cotiza a 10x el indice
    pre = df["fecha"] < FECHA_REESCALADO
    if pre.any():
        df.loc[pre, "close"] = df.loc[pre, "close"] / FACTOR_PRE_REESCALADO
        df.loc[pre, "settle"] = df.loc[pre, "settle"] / FACTOR_PRE_REESCALADO
    return (df[["fecha", "close", "settle", "volumen", "oi"]]
            .sort_values("fecha").reset_index(drop=True))


def _pedir(url, intentos):
    """Texto de la URL o None. Espera mas en cada intento fallido."""
    import urllib.request
    for intento in range(intentos):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            return urllib.request.urlopen(req, timeout=45).read().decode("utf-8", "replace")
        except Exception:
            if intento + 1 < intentos:
                time.sleep(PAUSA_CBOE * (intento + 1) * 2)
    return None


def descargar_cboe(codigo, anio):
    """Archivo CDN antiguo (hasta ago-2018). DataFrame o None."""
    raw = _pedir(CBOE_URL % (codigo, anio % 100), REINTENTOS_CBOE)
    time.sleep(PAUSA_CBOE)
    return None if raw is None else _parsear_cboe(raw)


def descargar_cboe_hist(venc_teorico):
    """Fichero oficial por contrato (2013 en adelante). Devuelve (DataFrame,
    vencimiento_oficial) o (None, None).

    El fichero se llama por la fecha de VENCIMIENTO, asi que la fecha con la que
    responde ES el vencimiento oficial. Se prueba primero la regla (con reintentos:
    es casi siempre la buena) y despues +-1 y +-2 dias, una sola vez cada una. Una
    fecha que no existe devuelve 403, igual que un corte por volumen: por eso la de
    la regla se reintenta y las demas no."""
    base = pd.Timestamp(venc_teorico).normalize()
    for d in DESVIOS_VENC:
        v = base + pd.Timedelta(days=d)
        raw = _pedir(CBOE_HIST_URL % v.strftime("%Y-%m-%d"),
                     REINTENTOS_CBOE if d == 0 else 1)
        time.sleep(PAUSA_CBOE)
        if raw is None:
            continue
        df = _parsear_cboe(raw)
        if df is None:
            continue
        # Guarda: fuera cualquier sesion que aun no haya liquidado. El fichero no
        # deberia traerlas (se publica de madrugada), pero si un dia cambian eso, el
        # ultimo negociado de una sesion a medias no puede entrar como historia.
        df = df[[sesion_liquidada(f) for f in df["fecha"]]]
        if len(df) == 0:
            continue
        return df.reset_index(drop=True), v
    return None, None


def settlement_oficial(fecha):
    """Settlement OFICIAL de CBOE para una fecha. Devuelve DataFrame
    [vencimiento, precio] de los contratos MENSUALES, ordenado por vencimiento (M1..Mn),
    o None si la fecha esta fuera de la ventana que sirve el endpoint.

    Endpoint (gratis, sin login, hallado el 2026-09-21):
      https://www.cboe.com/us/futures/market_statistics/settlement/csv/?dt=YYYY-MM-DD
      Columnas: Product, Symbol, Expiration Date, Price
      Mensuales = Symbol tipo 'VX/<mes><anio>'; los semanales llevan numero ('VX38/U6').

    VENTANA: solo las ultimas ~3-4 semanas/meses. Mas atras el endpoint NO da error:
    va soltando contratos ya vencidos y devuelve una curva incompleta y CORRIDA (en
    2026-05-15 le faltaba el front-month, asi que su 'M1' era en realidad el M2). Y para
    fechas muy viejas (probado 2013-05-15) devuelve directamente los datos de HOY con la
    fecha que le pidas. Por eso la GUARDA de abajo es obligatoria: si el primer
    vencimiento no cae poco despues de la fecha pedida, el dato no sirve.

    Validado el 2026-09-21: coincide EXACTO (4 decimales, M1..M4) con la serie de este
    script en 2026-06-15, 07-15, 08-14 y 09-18.
    """
    import urllib.request, io as _io, re
    url = ("https://www.cboe.com/us/futures/market_statistics/settlement/csv/?dt=%s"
           % pd.Timestamp(fecha).strftime("%Y-%m-%d"))
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        raw = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
        df = pd.read_csv(_io.StringIO(raw))
    except Exception:
        return None
    if "Product" not in df.columns:
        return None
    df = df[(df["Product"] == "VX") & df["Symbol"].astype(str).str.match(r"^VX/")]
    if len(df) == 0:
        return None
    df = df.rename(columns={"Expiration Date": "vencimiento", "Price": "precio"})
    df["vencimiento"] = pd.to_datetime(df["vencimiento"], errors="coerce")
    df = df.dropna(subset=["vencimiento"]).sort_values("vencimiento")

    # GUARDA anti-dato-falso: el primer vencimiento devuelto tiene que ser EL QUE TOCA
    # segun el calendario CFE. Una guarda por "dias hasta el primer vencimiento" NO
    # vale: 33 dias es correcto el 18-sep (el de septiembre ya vencio) y es una curva
    # corrida el 15-may (falta el de mayo). Hay que comparar contra el calendario.
    esperado = proximo_vencimiento(fecha)
    real = df["vencimiento"].iloc[0]
    if abs((real - esperado).days) > 3:      # margen por festivos
        return None
    return df[["vencimiento", "precio"]].reset_index(drop=True)


def sesion_liquidada(fecha):
    """True si la sesion del CFE fechada 'fecha' ya ha liquidado.

    El settlement de los futuros del VIX es a las 16:15 de Nueva York. Se compara
    en el huso del exchange y no en el local, para que no dependa de donde corra
    esto ni del cambio de hora (que no cae el mismo dia a los dos lados)."""
    from zoneinfo import ZoneInfo
    ny = ZoneInfo("America/New_York")
    d = pd.Timestamp(fecha)
    corte = dt.datetime(d.year, d.month, d.day, 16, 15, tzinfo=ny)
    return dt.datetime.now(ny) >= corte


def obtener_contrato(nombre, anio, mes, codigo, refresh, solo_cboe, dry_run,
                     vivo=False):
    """Devuelve (DataFrame, fuente).

    REGLA (usuario, 2026-09-21):
      - contrato VENCIDO  -> cache siempre que exista. Su precio ya no puede
        cambiar: la cache no es un fallback, es el archivo. Re-descargarlos a
        diario son ~6 min de trafico inutil y reactiva los 403 de CBOE.
      - contrato VIVO -> SIEMPRE fresco, sin caer a la cache. Si no se puede
        bajar, quien llama debe PARAR (no hay dato de hoy y publicar el de ayer
        como si fuera de hoy es justo lo que mato al sistema anterior).
    Y desde el 2026-09-23: la cache solo vale si su fuente es OFICIAL. Una cache
    de TradingView se ignora y el contrato se vuelve a bajar del CBOE.
    `solo_cboe` se conserva por compatibilidad: ya todo es CBOE.
    """
    ruta = os.path.join(CACHE, nombre + ".csv")
    if os.path.exists(ruta) and not refresh and not vivo:
        df = pd.read_csv(ruta)
        df["fecha"] = pd.to_datetime(df["fecha"])
        fuente = df["fuente"].iloc[0] if "fuente" in df.columns and len(df) else "cache"
        if fuente in FUENTES_OFICIALES:
            return df, fuente

    if dry_run:
        return None, "dry-run"

    df, fuente, venc_oficial = None, None, None
    if (anio, mes) <= CBOE_HASTA:
        df = descargar_cboe(codigo, anio)
        if df is not None:
            fuente = "CBOE"
            # El archivo antiguo esta TRUNCADO para los contratos de marzo a agosto
            # de 2018: los seis acaban el 2018-02-23. Sin esto, medio ano se quedaba
            # sin M1..M6 y la serie no protestaba, solo dejaba huecos. La cola sale
            # ahora del fichero oficial nuevo (antes, de TradingView).
            venc = pd.Timestamp(vencimiento_teorico(anio, mes))
            ya_vencido = venc < pd.Timestamp(dt.date.today())
            corte = df["fecha"].max()
            if ya_vencido and (venc - corte).days > TOL_COLA:
                cola, venc_oficial = descargar_cboe_hist(venc)
                if cola is not None:
                    cola = cola[cola["fecha"] > corte]
                    if len(cola):
                        df = pd.concat([df, cola], ignore_index=True)
                        df = df.sort_values("fecha").reset_index(drop=True)
                        fuente = "CBOE+HIST"
                        print("      %s: el archivo antiguo cortaba el %s y el vencimiento "
                              "es el %s -> +%d dias del fichero oficial"
                              % (nombre, corte.date(), venc.date(), len(cola)))
    if df is None:
        df, venc_oficial = descargar_cboe_hist(vencimiento_teorico(anio, mes))
        if df is not None:
            fuente = "CBOE_HIST"

    if df is None:
        return None, None

    df["fuente"] = fuente
    df["venc_oficial"] = venc_oficial if venc_oficial is not None else pd.NaT
    csv_atomico(df, ruta, index=False)
    return df, fuente


# ---------------------------------------------------------------------------
# construccion
# ---------------------------------------------------------------------------
def construir(refresh=False, solo_cboe=False, limite=None, dry_run=False):
    contratos = lista_contratos()
    if limite:
        contratos = contratos[:limite]

    print("Contratos a procesar: %d  (%s .. %s)"
          % (len(contratos), contratos[0][0], contratos[-1][0]))
    if dry_run:
        print("DRY-RUN: no se descarga ni se escribe nada.")
        for nombre, a, m, cod, venc in contratos[:5]:
            origen = "CBOE" if (a, m) <= CBOE_HASTA else "CBOE_HIST"
            print("  %s  venc_teorico=%s  fuente_prevista=%s" % (nombre, venc, origen))
        print("  ... y %d mas" % max(0, len(contratos) - 5))
        return None

    trozos = []
    resumen_fuente = {}
    fallidos = []
    fallidos_vivos = []
    no_listados = []       # mas alla de M8: el exchange aun no los ha sacado
    hoy_ts = pd.Timestamp(dt.date.today())
    # OBLIGATORIO = el contrato ocupa HOY uno de los huecos M1..M8. Ojo al matiz:
    # no basta con "aun no ha vencido". Los contratos a mas de 8 meses vista (p.ej.
    # VXM2027 en sep-2026) el CFE todavia NO los lista, asi que exigirlos frescos
    # pararia el proceso por una ausencia que no es un fallo. Solo son obligatorios
    # los que hacen falta para la curva de hoy.
    _esc_hoy = escalera_vencimientos(hoy_ts, hoy_ts + pd.Timedelta(days=400))
    _base_hoy = _esc_hoy.searchsorted(hoy_ts, side="right")
    for i, (nombre, a, m, cod, venc_teo) in enumerate(contratos, 1):
        _r = int(_esc_hoy.searchsorted(pd.Timestamp(venc_teo), side="right")) - _base_hoy
        vivo = 1 <= _r <= N_MESES
        df, fuente = obtener_contrato(nombre, a, m, cod, refresh, solo_cboe,
                                      dry_run, vivo=vivo)
        if df is None:
            if _r > N_MESES:
                # por delante de la curva util: se intenta por adelantado para
                # cogerlo el dia que el CFE lo liste, y que no este es lo normal.
                no_listados.append(nombre)
            else:
                fallidos.append(nombre)
                if vivo:
                    fallidos_vivos.append(nombre)
                print("  [%3d/%3d] %-10s FALTA%s"
                      % (i, len(contratos), nombre, "  <-- VIVO" if vivo else ""))
            continue
        df = df.copy()
        df["contrato"] = nombre
        df["venc_teorico"] = pd.Timestamp(venc_teo)
        trozos.append(df)
        resumen_fuente[fuente] = resumen_fuente.get(fuente, 0) + 1
        if i % 20 == 0 or i == len(contratos):
            print("  [%3d/%3d] %-10s %s  %d filas  %s -> %s"
                  % (i, len(contratos), nombre, fuente, len(df),
                     df["fecha"].min().date(), df["fecha"].max().date()))

    if not trozos:
        estado.fallar("descarga", "No se ha podido descargar ningun contrato.")

    # PARADA DURA: si falta un contrato VIVO no hay curva correcta que publicar.
    # Sin fallback a cache y sin escribir nada (decision del usuario 2026-09-21).
    if fallidos_vivos:
        estado.fallar(
            "descarga",
            "Faltan %d contratos necesarios para la curva de HOY (M1..M8) y no se "
            "publica nada: %s."
            % (len(fallidos_vivos), ", ".join(fallidos_vivos)),
            {"fallidos_vivos": fallidos_vivos, "fallidos_total": len(fallidos)})

    largo = pd.concat(trozos, ignore_index=True)

    # ventana pedida: nada anterior a FECHA_INICIO
    antes = len(largo)
    largo = largo[largo["fecha"] >= FECHA_INICIO]
    if antes != len(largo):
        print("\nRecorte por ventana (>= %s): %d filas fuera, quedan %d"
              % (FECHA_INICIO.date(), antes - len(largo), len(largo)))

    # vencimiento efectivo, por orden de confianza:
    #   1) el OFICIAL: el nombre del fichero del CBOE con el que respondio el contrato;
    #   2) contratos del archivo antiguo ya vencidos: su ultima fecha, que en ese
    #      archivo incluye el dia de liquidacion;
    #   3) el resto (no deberia quedar ninguno vivo sin fecha oficial): la regla.
    hoy = pd.Timestamp(dt.date.today())
    ultima_obs = largo.groupby("contrato")["fecha"].max()
    venc_teo = largo.groupby("contrato")["venc_teorico"].first()
    if "venc_oficial" not in largo.columns:
        largo["venc_oficial"] = pd.NaT
    largo["venc_oficial"] = pd.to_datetime(largo["venc_oficial"], errors="coerce")
    venc_of = largo.groupby("contrato")["venc_oficial"].first()
    vencido = ultima_obs < (hoy - pd.Timedelta(days=5))
    venc_efec = venc_teo.copy()
    venc_efec[vencido] = ultima_obs[vencido]
    tiene_of = venc_of.notna()
    venc_efec[tiene_of] = venc_of[tiene_of]

    # control de sanidad: regla vs fecha efectiva
    dif = (venc_teo - venc_efec).dt.days.abs()
    print("\nControl vencimientos (n=%d, %d con fecha oficial del CBOE):"
          % (len(dif), int(tiene_of.sum())))
    print("  regla = efectiva: %d   |  difiere 1-3 dias: %d  |  difiere >3 dias: %d"
          % ((dif == 0).sum(), ((dif > 0) & (dif <= 3)).sum(), (dif > 3).sum()))
    for c in dif[dif > 0].index:
        print("    %s  regla %s  efectiva %s%s"
              % (c, venc_teo[c].date(), venc_efec[c].date(),
                 "  (oficial)" if tiene_of.get(c, False) else ""))

    largo["vencimiento"] = largo["contrato"].map(venc_efec)

    # PRECIO CANONICO = SETTLE (liquidacion oficial), con Close solo de respaldo para
    # algun dia suelto sin Settle del archivo antiguo. Medido contra la pestana
    # Inclinacion del VIX Studio (2026-09-21): en el tramo CBOE su serie coincide
    # 99.9% con Settle y solo 50.7% con Close.
    settle = pd.to_numeric(largo["settle"], errors="coerce")
    close = pd.to_numeric(largo["close"], errors="coerce")
    largo["precio"] = settle.where(settle > 0, close)
    largo = largo.dropna(subset=["precio"])
    largo = largo[largo["precio"] > 0]

    # divergencia Close vs Settle en el tramo CBOE (para saber cuanto pesa la eleccion)
    cb = largo[(largo["fuente"] == "CBOE") & largo["settle"].notna()]
    if len(cb):
        d = (pd.to_numeric(cb["close"], errors="coerce")
             - pd.to_numeric(cb["settle"], errors="coerce")).abs()
        print("\nClose vs Settle en el tramo CBOE (n=%d):" % len(d))
        print("  identicos: %.2f%%   |  dif mediana: %.4f   |  dif max: %.4f"
              % (100.0 * (d < 1e-9).mean(), d.median(), d.max()))

    # FILTRO DE DIAS FANTASMA (anadido 2026-09-21, acotado el 2026-09-23).
    # Existia por TradingView, que emite barras para la sesion nocturna del domingo y
    # para algun festivo. Con fuentes OFICIALES ya no aplica: si el exchange publico
    # una liquidacion para una fecha, esa fecha es una sesion, diga lo que diga la
    # libreria de calendario. Aplicarle el filtro borraria dias buenos (la libreria
    # da por cerrado, por ejemplo, el 2018-12-05, y el CBOE liquido). Por eso ahora
    # solo filtra lo que NO sea oficial; con lo oficial solo cuenta y lo dice.
    # Los sabados y domingos se tiran siempre: ahi no hay liquidacion posible.
    finde = largo["fecha"].dt.dayofweek >= 5
    if finde.any():
        print("\nAVISO: %d filas en sabado/domingo descartadas" % int(finde.sum()))
        largo = largo[~finde]
    oficial = largo["fuente"].isin(FUENTES_OFICIALES)
    antes_cal = len(largo)
    dias_ok = None
    try:
        import pandas_market_calendars as mcal
        for nombre in ("CFE", "NYSE"):
            try:
                cal = mcal.get_calendar(nombre)
                sched = cal.schedule(start_date=largo["fecha"].min(),
                                     end_date=largo["fecha"].max())
                dias_ok = set(pd.DatetimeIndex(sched.index).normalize())
                print("\nCalendario de mercado usado: %s (%d sesiones)" % (nombre, len(dias_ok)))
                break
            except Exception:
                continue
    except ImportError:
        pass
    if dias_ok:
        fuera_cal = ~largo["fecha"].isin(dias_ok)
        if (fuera_cal & oficial).any():
            print("  %d filas OFICIALES en dias que el calendario da por cerrados "
                  "(se conservan: manda el exchange): %s"
                  % (int((fuera_cal & oficial).sum()),
                     ", ".join(sorted(set(str(d.date()) for d in
                                          largo.loc[fuera_cal & oficial, "fecha"])))[:200]))
        largo = largo[~fuera_cal | oficial]
    else:
        print("\nSin libreria de calendario: solo se filtran sabados y domingos.")
    if antes_cal != len(largo):
        print("  filas NO oficiales en dias sin sesion descartadas: %d"
              % (antes_cal - len(largo)))

    # pivote: por cada fecha, ordenar contratos vivos por vencimiento -> M1..M8
    # ROLL: el contrato deja de ser M1 EL MISMO dia de su vencimiento (liquida por la
    # manana). Se usa vencimiento > fecha, no >=. Medido contra el VIX Studio: con >=
    # aparecian 141 dias de desfase, justo los dias de vencimiento.
    largo = largo.sort_values(["fecha", "vencimiento"])
    vivos = largo[largo["vencimiento"] > largo["fecha"]].copy()

    # HUECOS POR CALENDARIO (arreglo del bug 1, auditoria 2026-09-21).
    # Antes: rank(method="first") numeraba densamente lo que hubiera llegado, asi
    # que un contrato ausente hacia SUBIR UNA POSICION a todos los de detras y se
    # publicaba una curva mal etiquetada con pinta de correcta (6 de 8 columnas
    # alteradas al simular la caida de un solo contrato).
    # Ahora: cada contrato se ancla al peldano de vencimiento que le toca en el
    # calendario, y su hueco M1..M8 es cuantos peldanos hay entre hoy y el suyo.
    # Si falta un contrato, su hueco queda VACIO y los demas no se mueven.
    # El mes de entrega de un contrato lo dice SU PROPIO NOMBRE (VXV2026 = octubre
    # 2026), no hay que deducirlo de sus datos. La primera version anclaba por el
    # vencimiento OBSERVADO (la ultima fecha con precio) y eso provocaba 659
    # colisiones: los contratos poco negociados dejan de imprimir precio semanas
    # antes de vencer y caian en el peldano del mes anterior, chocando con el
    # contrato que si le correspondia. Usando `venc_teorico`, que sale del nombre,
    # dos contratos NUNCA pueden compartir peldano: hay uno por mes.
    esc = escalera_vencimientos(vivos["fecha"].min(),
                                vivos["venc_teorico"].max() + pd.Timedelta(days=40))
    vivos["venc_cal"] = pd.DatetimeIndex(vivos["venc_teorico"])
    ie = esc.searchsorted(pd.DatetimeIndex(vivos["venc_cal"]), side="right")
    if_ = esc.searchsorted(pd.DatetimeIndex(vivos["fecha"]), side="right")
    vivos["rango"] = (ie - if_).astype(int)
    vivos = vivos[(vivos["rango"] >= 1) & (vivos["rango"] <= N_MESES)]
    # un mismo hueco no puede tener dos contratos: si pasa, el calendario y los
    # datos discrepan y es mejor parar que publicar una curva ambigua
    dup = vivos.duplicated(["fecha", "rango"]).sum()
    if dup:
        estado.fallar("pivote",
                      "%d colisiones fecha/hueco al asignar M1..M8 por calendario."
                      % dup)

    precios = vivos.pivot(index="fecha", columns="rango", values="precio")
    precios.columns = ["M%d" % c for c in precios.columns]
    vencs = vivos.pivot(index="fecha", columns="rango", values="vencimiento")
    vencs.columns = ["VENC_M%d" % c for c in vencs.columns]

    limpio = precios.reset_index().rename(columns={"fecha": "Fecha"})
    detalle = precios.join(vencs).reset_index().rename(columns={"fecha": "Fecha"})
    detalle["N_CONTRATOS"] = precios.notna().sum(axis=1).values

    return largo, limpio, detalle, fallidos, resumen_fuente, no_listados


def main():
    args = sys.argv[1:]
    refresh = "--refresh" in args
    solo_cboe = "--solo-cboe" in args
    dry_run = "--dry-run" in args
    limite = None
    if "--limite" in args:
        limite = int(args[args.index("--limite") + 1])

    os.makedirs(CACHE, exist_ok=True)
    r = construir(refresh=refresh, solo_cboe=solo_cboe, limite=limite, dry_run=dry_run)
    if r is None:
        return
    largo, limpio, detalle, fallidos, resumen_fuente, no_listados = r

    f_largo = os.path.join(DATA, "vix_contratos_largo.csv")
    f_limpio = os.path.join(DATA, "vix_futuros_M1_M8.csv")
    f_det = os.path.join(DATA, "vix_futuros_M1_M8_detalle.csv")

    # HOMOGENEIDAD: la historia es SOLO liquidacion oficial. Si algo no oficial se
    # colara (una cache vieja, una fuente nueva mal cableada), se para: mezclar dos
    # definiciones de precio en la misma vara es justo lo que se acaba de arreglar.
    no_oficial = ~largo["fuente"].isin(FUENTES_OFICIALES)
    if no_oficial.any():
        estado.fallar("descarga",
                      "%d filas de fuente NO oficial (%s): la historia tiene que ser "
                      "solo liquidacion del CBOE. No se escribe nada."
                      % (int(no_oficial.sum()),
                         ", ".join(sorted(largo.loc[no_oficial, "fuente"].astype(str).unique()))))

    for c in ("volumen", "oi"):
        if c not in largo.columns:
            largo[c] = np.nan
    csv_atomico(largo[["fecha", "contrato", "vencimiento", "close", "settle",
                       "fuente", "volumen", "oi"]], f_largo, index=False)
    csv_atomico(limpio, f_limpio, index=False)
    csv_atomico(detalle, f_det, index=False)

    print("\n" + "=" * 70)
    print("Contratos por fuente: %s" % resumen_fuente)
    if fallidos:
        print("Contratos NO descargados (%d): %s" % (len(fallidos), ", ".join(fallidos)))
    if no_listados:
        print("Aun no listados por el exchange (%d, normal): %s"
              % (len(no_listados), ", ".join(no_listados)))
    print("Filas en formato largo: %d" % len(largo))
    print("Fechas en la serie M1..M8: %d  (%s -> %s)"
          % (len(limpio), limpio["Fecha"].min().date(), limpio["Fecha"].max().date()))
    cols = [c for c in limpio.columns if c.startswith("M")]
    completas = limpio[cols].notna().all(axis=1).sum()
    print("Fechas con los %d meses completos: %d (%.1f%%)"
          % (N_MESES, completas, 100.0 * completas / max(1, len(limpio))))

    # CHIVATO (no filtro): desde sep-2018 el precio viene con 4 decimales porque
    # es el settlement. Una fila entera con <=2 decimales huele a sesion en curso.
    # Se avisa y no se tira: un settlement puede acabar en .85 por casualidad.
    def _dec(x):
        if pd.isna(x):
            return -1
        t = ("%.10f" % float(x)).rstrip("0")
        return len(t.split(".")[1]) if "." in t else 0
    reciente = limpio[limpio["Fecha"] >= pd.Timestamp("2018-09-01")]
    if len(reciente):
        md = reciente[cols].apply(lambda c: c.map(_dec)).max(axis=1)
        raras = reciente.loc[md[md <= 2].index, "Fecha"]
        if len(raras):
            print("AVISO: %d fila(s) con <=2 decimales desde sep-2018 (posible sesion "
                  "en curso): %s"
                  % (len(raras), ", ".join(str(d.date()) for d in raras[-5:])))
    estado.escribir(True, "descarga", "serie construida",
                    {"sesiones": int(len(limpio)),
                     "ultima_fecha": str(limpio["Fecha"].max().date()),
                     "contratos_fallidos": fallidos,
                     "aun_no_listados": no_listados,
                     "meses_ultimo_dia": int(limpio.iloc[-1][
                         [c for c in limpio.columns if c.startswith("M")]].notna().sum())},
                    marca_ok=False)   # una etapa intermedia no certifica la cadena

    print("\nEscrito:")
    print("  %s" % f_limpio)
    print("  %s" % f_det)
    print("  %s" % f_largo)


if __name__ == "__main__":
    main()
