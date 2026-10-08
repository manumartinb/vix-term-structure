"""
aviso_semaforos.py -- aviso de LUZ VERDE de los semaforos de entrada (semaforos.py).

CUANDO
Tarea "VIX CURVE Semaforos 15h15": lunes a viernes a las 15:15 de Madrid. Es antes de la
apertura de Nueva York (9:15 NY) y, en las semanas de desfase de cambio de hora de
octubre/noviembre y marzo, sigue siendo antes de las 10:30 NY (10:15 NY), que es la hora de
entrada de la LONG PUT. El cierre oficial que manda ya esta publicado desde la corrida de las
08:00 (reintento 11:00).

QUE HACE
Evalua todos los semaforos con el ULTIMO CIERRE OFICIAL. Si alguno esta en VERDE, manda un
Telegram con la accion del dia. Si ninguno lo esta, NO manda nada (el resumen diario va en el
aviso de las 17:00). No manda nada si hoy no es sesion del CFE. Si el cierre oficial esta
atrasado (no es el de la sesion anterior), lo dice en el mensaje.

USO
  python aviso_semaforos.py              # evalua y avisa solo si hay verde
  python aviso_semaforos.py --dry-run    # imprime el mensaje, no envia
  python aviso_semaforos.py --prueba     # envia SIEMPRE, marcado como PRUEBA (no es senal)

Reglas tecnicas del proyecto: ASCII, cp1252. Nada de '<' ni '>' sueltos en el texto (parse_mode HTML).
"""

import sys
import datetime as dt

import pandas as pd

import estado
import semaforos
import percentiles_curva as pcv
import descargar_futuros_vix as dfv

URL_WEB = "https://manumartinb.github.io/vix-term-structure/"


def es_sesion(d):
    hab = dfv._habiles_cfe()
    d = pd.Timestamp(d).normalize()
    return (d in hab) if hab else d.weekday() < 5


def sesion_anterior(d):
    d = pd.Timestamp(d).normalize()
    for k in range(1, 15):
        c = d - pd.Timedelta(days=k)
        if es_sesion(c):
            return c
    return d - pd.Timedelta(days=1)


def mensaje(verdes, todos, atrasado, esperado, prueba=False):
    L = []
    if prueba:
        L.append("<b>PRUEBA del aviso de semaforos (no es senal)</b>")
    for s in verdes:
        fmt = s["formato"]
        L.append("\U0001F7E2 <b>LUZ VERDE: %s</b>" % s["nombre"])
        L.append("Cierre oficial %s: %s %s (%s en verde)."
                 % (semaforos.fecha_corta(s["fecha_cierre"]), s["variable"],
                    semaforos.txt_valor(fmt, s["valor"]),
                    "1 sesion" if s["racha"] == 1 else "%d sesiones" % s["racha"]))
        L.append("Entrada: " + s["accion_si"])
        L.append("Salida: " + s["salida"])
        L.append("")
    if prueba and not verdes:
        for s in todos:
            L.append("%s: %s (cierre %s: %s %s)." % (s["nombre"], "VERDE" if s.get("estado") == "SI" else "no",
                                                    semaforos.fecha_corta(s.get("fecha_cierre")), s["variable"],
                                                    semaforos.txt_valor(s["formato"], s.get("valor"))))
    if atrasado:
        L.append("OJO: el ultimo cierre oficial no es el de la sesion anterior (%s): el dato puede estar atrasado."
                 % esperado.strftime("%d/%m"))
    L.append('<a href="%s">Abrir panel</a>' % URL_WEB)
    return "\n".join(L)


def main():
    args = sys.argv[1:]
    prueba = "--prueba" in args
    hoy = pd.Timestamp(dt.date.today())
    if not es_sesion(hoy) and not prueba:
        print("%s: hoy no es sesion del CFE, no se avisa." % hoy.date())
        return
    serie = pcv.cargar_serie()
    todos = semaforos.evaluar_todos(serie)
    esperado = sesion_anterior(hoy)
    atrasado = pd.Timestamp(serie.index[-1]).normalize() < esperado
    verdes = [s for s in todos if s.get("estado") == "SI"]
    for s in todos:
        print("%s: %s (cierre %s, %s %s)" % (s["nombre"], s.get("estado"), s.get("fecha_cierre"), s["variable"],
                                             semaforos.txt_valor(s["formato"], s.get("valor"))))
    if not verdes and not prueba:
        print("Ningun semaforo en verde: no se envia nada.")
        return
    txt = mensaje(verdes, todos, atrasado, esperado, prueba)
    if "--dry-run" in args:
        print("--dry-run, mensaje:\n" + txt)
        return
    if estado.avisar(txt):
        print("Aviso enviado (%d en verde%s)." % (len(verdes), ", PRUEBA" if prueba else ""))
    else:
        print("FALLO: el aviso NO se envio.")
        sys.exit(1)


if __name__ == "__main__":
    main()
