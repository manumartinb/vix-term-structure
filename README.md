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
| 08:00 (Madrid), todos los días; reintento a las 11:00 | Corrida **firme**: liquidación oficial de la sesión anterior, percentiles recalculados, panel y CSV regenerados |

Las 08:00 no son arbitrarias: el Cboe publica la liquidación de cada sesión en sus
ficheros oficiales de madrugada (medido: a las 07:06 de Madrid). La corrida de las 11:00
es la red por si un día lo cuelga tarde; si a esa hora sigue sin estar, se avisa. Los
domingos, los lunes y los días siguientes a un festivo no hay sesión nueva y la corrida
termina sin hacer nada.

Si un paso de la corrida falla, **no se publica nada** y queda constancia en
`data/estado.json`. Es deliberado: una página con el dato de ayer y un aviso es mejor
que una página con un dato inventado y silencio.

### La banda EN VIVO

Durante la sesion, arriba del panel aparece la lectura del momento. Tres cosas que
conviene entender antes de usarla:

1. **Es provisional.** Se compara contra el historico *cerrado*: el dia de hoy nunca
   entra en la distribucion que le sirve de vara. El numero firme es la liquidación
   oficial, que entra a la mañana siguiente, y no tiene por que coincidir, porque son dos
   precios distintos del mismo dia. Es lo único que sigue viniendo de TradingView, y no se
   guarda nunca en la historia.
2. **Se dice a que hora se pidio** y cuanto duro la ventana. Los 8 vencimientos se piden
   de uno en uno, no en paralelo: con 8 peticiones simultaneas el proveedor devuelve
   "sin datos" para algunas, y ese hueco falso es mucho peor que tardar 4 segundos.
3. **Cada pata lleva su antiguedad.** M7 y M8 apenas se negocian intradia; cuando su
   ultima vela pasa de 2 horas se usa el cierre anterior y se dice. Las parejas que
   dependan de una pata asi salen marcadas con `*` y **no disparan alerta**.

## Fuentes

Toda la serie es **liquidación oficial** (settlement) del **Cboe Futures Exchange**,
contrato a contrato. No de cierre: difieren en tres de cada cuatro días.

- **Abril 2007 → febrero 2018**: archivo público del exchange
  (`cdn.cboe.com/resources/futures/archive/volume-and-price/`).
- **Todo lo demás**: el fichero oficial por contrato del exchange
  (`cdn.cboe.com/data/us/futures/market_statistics/historical_data/VX/`), que cubre los
  contratos que vencen desde 2013. Rellena lo que el archivo antiguo no tiene: los
  contratos de septiembre de 2018 en adelante, 16 contratos de 2014-2017 que ese archivo no
  sirve, y la cola de los de marzo a agosto de 2018, que corta el 23-feb-2018.
- Donde las dos fuentes se solapan (6.909 precios de 2013 a 2018) coinciden **al cuarto
  decimal**. El vencimiento de cada contrato es el que publica el propio exchange.

### Corrección del 23-sep-2026: desfase de fechas en el tramo de TradingView

Hasta el 23-sep-2026, el tramo de septiembre de 2018 en adelante (y esos 16 contratos de
2014-2017) venía de TradingView. Al contrastarlo con la liquidación oficial apareció un
**desfase de fecha**: la librería que lo descargaba fechaba cada vela con la hora local de
la máquina (Madrid), y la vela diaria de un futuro lleva la hora de *apertura* de su sesión,
las 17:00 de Chicago del día anterior. En las ~3 semanas al año en que EEUU y Europa cambian
la hora en fechas distintas (marzo y final de octubre), eso caía antes de medianoche y
**cada día guardaba la liquidación del día siguiente** (1.039 precios en 174 días, hasta
casi 12 puntos de diferencia en marzo de 2020). Además faltaban 47 sesiones y los nueve
vencimientos de marzo de 2018 a 2026 iban un día antes.

La serie se ha rehecho entera con la liquidación oficial. Fuera de esas semanas los
percentiles apenas se mueven; dentro, uno de cada cinco se desviaba más de 5 puntos.

### Verificación independiente

Dos comprobaciones, ambas contra fuentes que no participan en construir esta serie:

1. **Contra el endpoint oficial de settlement del exchange**
   (`cboe.com/us/futures/market_statistics/settlement/csv/?dt=…`), en las fechas recientes
   que todavía sirve (15-jun, 15-jul, 14-ago y 18-sep de 2026): coincide **al cuarto
   decimal** en M1 a M4.
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
4. **Días sin sesión.** Las fechas son las del propio exchange: solo hay fila si el Cboe
   publicó liquidación ese día. (Otros proveedores emiten barras para la sesión nocturna
   del domingo o para festivos, y fechan las velas en su propio huso: es justo lo que
   desfasaba la versión anterior de esta serie.)

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
