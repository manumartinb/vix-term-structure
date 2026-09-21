# Curva de futuros VIX — percentil histórico por par

Histórico diario de los 8 primeros vencimientos de futuros del VIX (M1–M8) desde
**abril de 2007**, y el percentil histórico *expanding* de cada uno de los **28 pares**
Mj/Mi que se pueden formar con ellos.

**Panel en vivo:** https://manumartinb.github.io/vix-term-structure/

## Descargas

| Fichero | Qué contiene |
|---|---|
| [`data/vix_futuros_M1_M8.csv`](data/vix_futuros_M1_M8.csv) | Fecha + M1…M8. Precio de liquidación de cada vencimiento, un día por fila |
| [`data/vix_percentiles_pares.csv`](data/vix_percentiles_pares.csv) | Fecha + los 28 pares. Percentil expanding de cada par, día a día |

## Cómo leer el percentil

Para cada par Mj/Mi se calcula el ratio `Mj/Mi − 1` y se mira dónde queda ese valor
dentro de **toda su historia anterior a esa fecha** (expanding, sin mirar al futuro).
El resultado se invierte, siguiendo la convención del panel original:

- **100** → el tramo está lo más cerca posible de *backwardation*: el vencimiento corto
  caro respecto al largo. Típico de pánico.
- **0** → *contango* extremo: el largo mucho más caro que el corto. Típico de calma.

Los primeros 250 días de cada serie no se publican: un percentil contra tan pocas
observaciones no significa nada.

## Fuentes

- **Abril 2007 → agosto 2018**: archivo público oficial del **Cboe Futures Exchange**,
  contrato a contrato (`cdn.cboe.com/resources/futures/archive/volume-and-price/`).
  Precio de **liquidación** (settlement), no de cierre.
- **Septiembre 2018 → hoy**: TradingView, contrato a contrato por vencimiento. Publica
  el mismo precio de liquidación: verificado contra el endpoint de settlement oficial
  del exchange en las fechas recientes que todavía sirve — coincide **al cuarto decimal**.

### Validación

La serie se contrastó día a día contra el settlement oficial del exchange en las **2.436
sesiones** en las que éste está disponible: **98,77 % de coincidencia exacta**.

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
