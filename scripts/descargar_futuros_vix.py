"""
descargar_futuros_vix.py -- construye la serie historica M1..M8 de futuros VIX.

QUE HACE
Descarga el historico de CADA contrato mensual de futuros VIX y, para cada dia de
negociacion, ordena los contratos vivos por fecha de vencimiento y los reparte en
columnas M1..M8 (M1 = el mas cercano a vencer).

VENTANA: desde 2007-04-01 hasta hoy (decision del usuario, 2026-09-21). Lo anterior
se descarta: evita el reescalado del 2007-03-26 (ver mas abajo) y los anios en que
CFE aun no listaba meses suficientes para formar una curva de 8.

FUENTES (en este orden, por contrato)
  1) CBOE OFICIAL (archivo CDN):   contratos K04 (may-2004) .. Q18 (ago-2018)
     https://cdn.cboe.com/resources/futures/archive/volume-and-price/CFE_<CODE><YY>_VX.csv
     Columnas: Trade Date, Futures, Open, High, Low, Close, Settle, Change,
               Total Volume, EFP, Open Interest
     Verificado 2026-09-21: K04..Q18 responden 200; U18 en adelante dan 403.
  2) TradingView (tvDatafeed, sin login): contratos U18 (sep-2018) en adelante
     Simbolo VX<CODE><YYYY> en exchange CBOE (ej. VXV2026 = octubre 2026).
     Solo trae Close (no hay Settle).

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
contrato. Para contratos ya vencidos se usa la ultima fecha observada en su propio
fichero (es la verdad del dato); la regla solo se usa para los que siguen vivos.
El script compara las dos y reporta cuantas veces difieren (control de sanidad).

SALIDAS (carpeta data/)
  contratos/<CONTRATO>.csv          cache crudo por contrato (no se re-descarga)
  vix_contratos_largo.csv           formato largo: fecha, contrato, vencimiento, close, settle, fuente
  vix_futuros_M1_M8.csv             pivotado limpio: Fecha, M1..M8
  vix_futuros_M1_M8_detalle.csv     idem + vencimiento de cada mes + n contratos vivos

USO
  python descargar_futuros_vix.py                 # usa cache, descarga solo lo que falte
  python descargar_futuros_vix.py --refresh       # re-descarga todo (ignora cache)
  python descargar_futuros_vix.py --solo-cboe     # no toca TradingView (solo 2004-2018)
  python descargar_futuros_vix.py --limite 20     # solo los N primeros contratos (prueba rapida)
  python descargar_futuros_vix.py --dry-run       # no escribe nada, solo dice que haria

Reglas tecnicas del proyecto: ASCII, cp1252.
"""

import os
import sys
import time
import datetime as dt

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
CACHE = os.path.join(DATA, "contratos")

CBOE_URL = ("https://cdn.cboe.com/resources/futures/archive/"
            "volume-and-price/CFE_%s%02d_VX.csv")

# codigo de mes de futuros: 1=enero ... 12=diciembre
CODIGOS = ["F", "G", "H", "J", "K", "M", "N", "Q", "U", "V", "X", "Z"]

# ventana de la serie: el usuario descarta lo anterior a abril-2007 (decision 2026-09-21).
# Empezar el 2007-04-01 deja fuera por completo el reescalado del 2007-03-26 y arranca
# donde CFE ya listaba suficientes meses seguidos.
FECHA_INICIO = pd.Timestamp("2007-04-01")

# primer y ultimo contrato a intentar (J07 = primer contrato que vence tras FECHA_INICIO)
PRIMER_ANIO, PRIMER_MES = 2007, 4
ULTIMO_ANIO, ULTIMO_MES = 2027, 7      # margen para cubrir M8 desde hoy

# frontera medida el 2026-09-21: el archivo CDN de CBOE llega hasta Q18 (ago-2018)
CBOE_HASTA = (2018, 8)

N_MESES = 8          # M1..M8
PAUSA_TV = 1.5       # segundos entre llamadas a TradingView
REINTENTOS_TV = 3
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


def vencimiento_teorico(anio, mes):
    """Miercoles 30 dias antes del tercer viernes del mes siguiente (regla CFE)."""
    if mes == 12:
        a2, m2 = anio + 1, 1
    else:
        a2, m2 = anio, mes + 1
    return tercer_viernes(a2, m2) - dt.timedelta(days=30)


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


def lista_contratos():
    """[(nombre, anio, mes, codigo, vencimiento_teorico), ...] en orden cronologico."""
    out = []
    a, m = PRIMER_ANIO, PRIMER_MES
    while (a, m) <= (ULTIMO_ANIO, ULTIMO_MES):
        cod = CODIGOS[m - 1]
        out.append(("VX%s%04d" % (cod, a), a, m, cod, vencimiento_teorico(a, m)))
        m += 1
        if m > 12:
            a, m = a + 1, 1
    return out


# ---------------------------------------------------------------------------
# descarga
# ---------------------------------------------------------------------------
def descargar_cboe(codigo, anio):
    """Devuelve DataFrame [fecha, close, settle] o None. Fuente oficial del exchange."""
    import urllib.request
    url = CBOE_URL % (codigo, anio % 100)
    raw = None
    for intento in range(REINTENTOS_CBOE):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            raw = urllib.request.urlopen(req, timeout=45).read().decode("utf-8", "replace")
            break
        except Exception:
            # 403 por limitacion de volumen: esperar mas en cada intento
            time.sleep(PAUSA_CBOE * (intento + 1) * 2)
    time.sleep(PAUSA_CBOE)
    if raw is None:
        return None
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
    df = df.rename(columns={"Trade Date": "fecha", "Close": "close", "Settle": "settle"})
    if "fecha" not in df.columns:
        return None
    df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce")
    for c in ("close", "settle"):
        df[c] = pd.to_numeric(df.get(c), errors="coerce")
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
    return df[["fecha", "close", "settle"]].sort_values("fecha").reset_index(drop=True)


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


def descargar_tv(nombre):
    """Devuelve DataFrame [fecha, close, settle(NaN)] o None. TradingView, solo Close."""
    try:
        from tvDatafeed import TvDatafeed, Interval
    except Exception:
        return None
    for intento in range(REINTENTOS_TV):
        try:
            tv = TvDatafeed()
            df = tv.get_hist(symbol=nombre, exchange="CBOE",
                             interval=Interval.in_daily, n_bars=5000)
            if df is not None and len(df) > 0:
                out = pd.DataFrame({
                    "fecha": pd.to_datetime(df.index).normalize(),
                    "close": pd.to_numeric(df["close"], errors="coerce"),
                    "settle": pd.NA,
                })
                out = out[out["close"].fillna(0) > 0]
                if len(out) == 0:
                    return None
                return out.sort_values("fecha").reset_index(drop=True)
        except Exception:
            pass
        time.sleep(PAUSA_TV * (intento + 1))
    return None


def obtener_contrato(nombre, anio, mes, codigo, refresh, solo_cboe, dry_run):
    """Devuelve (DataFrame, fuente). Usa cache en disco si existe."""
    ruta = os.path.join(CACHE, nombre + ".csv")
    if os.path.exists(ruta) and not refresh:
        df = pd.read_csv(ruta)
        df["fecha"] = pd.to_datetime(df["fecha"])
        fuente = df["fuente"].iloc[0] if "fuente" in df.columns and len(df) else "cache"
        return df, fuente

    if dry_run:
        return None, "dry-run"

    df, fuente = None, None
    if (anio, mes) <= CBOE_HASTA:
        df = descargar_cboe(codigo, anio)
        if df is not None:
            fuente = "CBOE"
    if df is None and not solo_cboe:
        df = descargar_tv(nombre)
        if df is not None:
            fuente = "TV"
        time.sleep(PAUSA_TV)

    if df is None:
        return None, None

    df["fuente"] = fuente
    df.to_csv(ruta, index=False)
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
            origen = "CBOE" if (a, m) <= CBOE_HASTA else "TV"
            print("  %s  venc_teorico=%s  fuente_prevista=%s" % (nombre, venc, origen))
        print("  ... y %d mas" % max(0, len(contratos) - 5))
        return None

    trozos = []
    resumen_fuente = {}
    fallidos = []
    for i, (nombre, a, m, cod, venc_teo) in enumerate(contratos, 1):
        df, fuente = obtener_contrato(nombre, a, m, cod, refresh, solo_cboe, dry_run)
        if df is None:
            fallidos.append(nombre)
            print("  [%3d/%3d] %-10s FALTA" % (i, len(contratos), nombre))
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
        print("No se ha podido descargar ningun contrato.")
        return None

    largo = pd.concat(trozos, ignore_index=True)

    # ventana pedida: nada anterior a FECHA_INICIO
    antes = len(largo)
    largo = largo[largo["fecha"] >= FECHA_INICIO]
    if antes != len(largo):
        print("\nRecorte por ventana (>= %s): %d filas fuera, quedan %d"
              % (FECHA_INICIO.date(), antes - len(largo), len(largo)))

    # vencimiento efectivo: para contratos ya vencidos, la ultima fecha observada;
    # para los vivos, la regla teorica.
    hoy = pd.Timestamp(dt.date.today())
    ultima_obs = largo.groupby("contrato")["fecha"].max()
    venc_teo = largo.groupby("contrato")["venc_teorico"].first()
    vencido = ultima_obs < (hoy - pd.Timedelta(days=5))
    venc_efec = venc_teo.copy()
    venc_efec[vencido] = ultima_obs[vencido]

    # control de sanidad: regla vs observado en los ya vencidos
    dif = (venc_teo[vencido] - ultima_obs[vencido]).dt.days.abs()
    if len(dif):
        print("\nControl vencimientos (solo contratos vencidos, n=%d):" % len(dif))
        print("  coincide exacto: %d   |  difiere 1-3 dias: %d  |  difiere >3 dias: %d"
              % ((dif == 0).sum(), ((dif > 0) & (dif <= 3)).sum(), (dif > 3).sum()))

    largo["vencimiento"] = largo["contrato"].map(venc_efec)

    # PRECIO CANONICO = SETTLE (precio de liquidacion oficial), con Close de respaldo.
    # Medido contra la pestana Inclinacion del VIX Studio (2026-09-21): en el tramo
    # CBOE su serie coincide 99.9% con Settle y solo 50.7% con Close. En el tramo
    # TradingView no hay Settle, pero su Close diario ya ES el de liquidacion (87.1%
    # de coincidencia, y el resto se explica por el dia del roll).
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

    # FILTRO DE DIAS FANTASMA (anadido 2026-09-21).
    # TradingView emite barras diarias para la sesion nocturna del domingo y para
    # algun festivo: en la primera version colaron 45 filas en DOMINGO (0,9% de la
    # serie), fechas en las que el exchange no liquido nada. Ensucian la historia
    # contra la que se calculan los percentiles. Se filtran con el calendario real
    # del CFE; si la libreria no esta, al menos caen sabados y domingos.
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
        largo = largo[largo["fecha"].isin(dias_ok)]
    else:
        largo = largo[largo["fecha"].dt.dayofweek < 5]
        print("\nSin libreria de calendario: solo se filtran sabados y domingos.")
    if antes_cal != len(largo):
        print("  filas en dias sin sesion descartadas: %d" % (antes_cal - len(largo)))

    # pivote: por cada fecha, ordenar contratos vivos por vencimiento -> M1..M8
    # ROLL: el contrato deja de ser M1 EL MISMO dia de su vencimiento (liquida por la
    # manana). Se usa vencimiento > fecha, no >=. Medido contra el VIX Studio: con >=
    # aparecian 141 dias de desfase, justo los dias de vencimiento.
    largo = largo.sort_values(["fecha", "vencimiento"])
    vivos = largo[largo["vencimiento"] > largo["fecha"]].copy()
    vivos["rango"] = vivos.groupby("fecha")["vencimiento"].rank(method="first").astype(int)
    vivos = vivos[vivos["rango"] <= N_MESES]

    precios = vivos.pivot(index="fecha", columns="rango", values="precio")
    precios.columns = ["M%d" % c for c in precios.columns]
    vencs = vivos.pivot(index="fecha", columns="rango", values="vencimiento")
    vencs.columns = ["VENC_M%d" % c for c in vencs.columns]

    limpio = precios.reset_index().rename(columns={"fecha": "Fecha"})
    detalle = precios.join(vencs).reset_index().rename(columns={"fecha": "Fecha"})
    detalle["N_CONTRATOS"] = precios.notna().sum(axis=1).values

    return largo, limpio, detalle, fallidos, resumen_fuente


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
    largo, limpio, detalle, fallidos, resumen_fuente = r

    f_largo = os.path.join(DATA, "vix_contratos_largo.csv")
    f_limpio = os.path.join(DATA, "vix_futuros_M1_M8.csv")
    f_det = os.path.join(DATA, "vix_futuros_M1_M8_detalle.csv")

    largo[["fecha", "contrato", "vencimiento", "close", "settle", "fuente"]].to_csv(
        f_largo, index=False)
    limpio.to_csv(f_limpio, index=False)
    detalle.to_csv(f_det, index=False)

    print("\n" + "=" * 70)
    print("Contratos por fuente: %s" % resumen_fuente)
    if fallidos:
        print("Contratos NO descargados (%d): %s" % (len(fallidos), ", ".join(fallidos)))
    print("Filas en formato largo: %d" % len(largo))
    print("Fechas en la serie M1..M8: %d  (%s -> %s)"
          % (len(limpio), limpio["Fecha"].min().date(), limpio["Fecha"].max().date()))
    cols = [c for c in limpio.columns if c.startswith("M")]
    completas = limpio[cols].notna().all(axis=1).sum()
    print("Fechas con los %d meses completos: %d (%.1f%%)"
          % (N_MESES, completas, 100.0 * completas / max(1, len(limpio))))
    print("\nEscrito:")
    print("  %s" % f_limpio)
    print("  %s" % f_det)
    print("  %s" % f_largo)


if __name__ == "__main__":
    main()
