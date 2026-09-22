# Curva de futuros VIX — percentil histórico por par

Histórico diario de los 8 primeros vencimientos de futuros del VIX (M1–M8) desde
**abril de 2007**, y el percentil histórico *expanding* de cada uno de los **28 pares**
Mj/Mi que se pueden formar con ellos.

**Panel en vivo:** https://manumartinb.github.io/vix-term-structure/

## Descargas

| Fichero | Qué contiene |
|---|---|
| [`data/vix_futuros_M1_M8.csv`](data/vix_futuros_M1_M8.csv) | Fecha + M1…M8. Precio de liquidación de cada vencimiento, un día por fila |
| [`data/vix_percentiles_pares.csv`](data/vix_percentiles_pares.csv) | Fecha + los 28 pares. Percentil condicional por días a vencimiento, día a día |

## Cómo leer el percentil

Para cada par Mj/Mi se calcula el ratio `Mj/Mi − 1` y se mira dónde queda ese valor
dentro de su historia anterior a esa fecha (expanding, sin mirar al futuro). El
resultado se invierte, siguiendo la convención del panel original:

- **100** → el tramo está lo más cerca posible de *backwardation*: el vencimiento corto
  caro respecto al largo. Típico de pánico.
- **0** → *contango* extremo: el largo mucho más caro que el corto. Típico de calma.

### El percentil es CONDICIONAL a los días que faltan para el vencimiento

No se compara contra toda la historia mezclada, sino **solo contra los días que estaban
en el mismo punto del ciclo mensual** (mismos días hasta el vencimiento del front month,
ventana ±2, y todo lo que pase de 30 días en un mismo tramo).

Sin esta corrección el percentil mide el calendario y no el mercado: como M1 converge al
contado según se acerca su vencimiento, la base promediaba percentil 27 a 3 días del roll
y 61 a 30 días —**33,9 puntos de sesgo puramente mecánico**— y los pares que cuelgan de M1
arrastraban 13,0. Medido tras la corrección: 5,7 y 3,5.

**Consecuencia para la lectura**: un 95 significa *"extremo para este punto del ciclo"*, no
extremo en términos absolutos.

Un día no se publica hasta que su tramo reúne **100 observaciones comparables** previas.
Por eso la serie de percentiles arranca más tarde que la de precios, y algunos pares tienen
huecos sueltos.

## Actualizacion

| Cuando | Que pasa |
|---|---|
| Cada 15 min, 15:30-22:15 (Madrid), L-V | Lectura **en vivo**: los 8 vencimientos pedidos en una sola ventana de pocos segundos |
| 23:00 (Madrid), todos los dias | Corrida **firme**: settlement del dia, percentiles recalculados, panel y CSV regenerados |

Las 23:00 no son arbitrarias: los futuros del VIX liquidan a las 16:15 de Nueva York,
que son las 22:15 de Madrid, y los dos husos cambian de hora a la vez, asi que el
margen se mantiene todo el anio.

Si un paso de la corrida nocturna falla, **no se publica nada** y queda constancia en
`data/estado.json`. Es deliberado: una pagina con el dato de ayer y un aviso es mejor
que una pagina con un dato inventado y silencio.

### La banda EN VIVO

Durante la sesion, arriba del panel aparece la lectura del momento. Tres cosas que
conviene entender antes de usarla:

1. **Es provisional.** Se compara contra el historico *cerrado*: el dia de hoy nunca
   entra en la distribucion que le sirve de vara. El numero firme es el de la noche, y
   no tiene por que coincidir, porque son dos precios distintos del mismo dia.
2. **Se dice a que hora se pidio** y cuanto duro la ventana. Los 8 vencimientos se piden
   de uno en uno, no en paralelo: con 8 peticiones simultaneas el proveedor devuelve
   "sin datos" para algunas, y ese hueco falso es mucho peor que tardar 4 segundos.
3. **Cada pata lleva su antiguedad.** M7 y M8 apenas se negocian intradia; cuando su
   ultima vela pasa de 2 horas se usa el cierre anterior y se dice. Las parejas que
   dependan de una pata asi salen marcadas con `*` y **no disparan alerta**.

## Fuentes

- **Abril 2007 → agosto 2018**: archivo público del **Cboe Futures Exchange**, contrato a
  contrato (`cdn.cboe.com/resources/futures/archive/volume-and-price/`). Precio de
  **liquidación** (settlement), no de cierre.
  **Excepción**: 16 contratos —los de septiembre, octubre, noviembre y diciembre de 2014,
  2015, 2016 y 2017— no están en ese archivo (devuelve 403) y vienen de TradingView.
- **Septiembre 2018 → hoy**: TradingView, contrato a contrato por vencimiento.

### Verificación independiente

Dos comprobaciones, ambas contra fuentes que no participan en construir esta serie:

1. **Contra el endpoint oficial de settlement del exchange**
   (`cboe.com/us/futures/market_statistics/settlement/csv/?dt=…`), en las fechas recientes
   que todavía sirve (15-jun, 15-jul, 14-ago y 18-sep de 2026): coincide **al cuarto
   decimal** en M1 a M4. Es la comprobación más fuerte del tramo de TradingView, porque
   enfrenta dos proveedores distintos.
2. **Contra una serie construida aparte** por el autor mediante *scraping* de VIXCENTRAL:
   **94,3 % de las celdas idénticas** sobre 4.331 fechas solapadas, y de **2010 a 2013 sin
   una sola diferencia** en ~1.790 sesiones seguidas.

### Corrección de una cifra que estuvo publicada

Una versión anterior de esta página afirmaba *"98,77 % de coincidencia exacta contra el
settlement oficial en 2.436 sesiones"*. **Esa cifra se ha retirado por circular**: la serie
usa como precio el `settle` del fichero de CBOE y se estaba comparando contra ese mismo
`settle`. Lo que en realidad medía es algo distinto y más modesto —que el pivote asigna
correctamente los contratos a las casillas M1..M8— y como tal sí fue útil: destapó un fallo
por el que un contrato ausente desplazaba las etiquetas de toda la curva.

## Detalles de método que suelen fallar en otros datasets

1. **Reescalado de 2007.** Hasta el 23 de marzo de 2007 los futuros del VIX cotizaban a
   **diez veces** el índice; el 26 de marzo el exchange los reescaló (el contrato de abril
   cerró a 133,50 el día 23 y a 13,40 el 26). Esta serie arranca después del cambio para
   no mezclar escalas.
2. **Liquidación, no cierre.** `Close` y `Settle` difieren en tres de cada cuatro días.
   Aquí siempre se usa el de liquidación.
3. **El roll.** Un contrato deja de ser M1 **el mismo día** en que liquida, no al
   siguiente.
4. **Días sin sesión.** Se filtran con el calendario real del CFE: algunos proveedores
   emiten barras para la sesión nocturna del domingo, que no son días de liquidación.

## Reproducir

```
python scripts/descargar_futuros_vix.py    # descarga contratos y arma la serie (cachea)
python scripts/construir_sitio.py          # recalcula percentiles y regenera el panel
```

`scripts/percentiles_curva.py` es la librería con los 28 pares y el cálculo de percentil;
se puede usar suelta:

```
python scripts/percentiles_curva.py --fecha 2020-03-16
```

## Aviso

Datos de mercado públicos, recopilados sin garantía de exactitud y sin fin comercial.
Esto no es asesoramiento de inversión.
