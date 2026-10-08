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
Telegram con la accion del dia (la hora de Espana se calcula para el dia: 16:30 o 15:30). Si
algun semaforo esta SIN DATO (el ultimo cierre no trae sus patas), manda un aviso de que hoy no
se puede evaluar. Si no hay nada de eso, NO manda nada (el resumen diario va en el aviso de las
17:00). No manda nada si hoy no es sesion del CFE. Si el cierre oficial esta atrasado (no es el
de la sesion anterior), lo dice en el mensaje.

FIABILIDAD (auditoria 2026-10-08): hasta 3 intentos de envio (0, 20 y 60 s de espera); si los
tres fallan sale con codigo 1, que llega al Programador de tareas porque _vix_semaforos.vbs
espera al proceso (WScript.Quit sh.Run(..., True)). Los textos van escapados para HTML.

USO
  python aviso_semaforos.py              # evalua y avisa solo si hay verde o sin dato
  python aviso_semaforos.py --dry-run    # imprime el mensaje, no envia
  python aviso_semaforos.py --prueba     # envia SIEMPRE, marcado como PRUEBA (no es senal)

Reglas tecnicas del proyecto: ASCII, cp1252.
"""

import sys
import time
import datetime as dt

import pandas as pd

import estado
import semaforos
import percentiles_curva as pcv
import descargar_futuros_vix as dfv

URL_WEB = "https://manumartinb.github.io/vix-term-structure/"
ESPERAS_REINTENTO = (0, 20, 60)
esc = semaforos.esc


def es_sesion(d):
    """Sesion del CFE. Fuera del alcance del calendario (o sin calendario): de lunes a viernes."""
    hab = dfv._habiles_cfe()
    d = pd.Timestamp(d).normalize()
    if hab and min(hab) <= d <= max(hab):
        return d in hab
    return d.weekday() < 5


def sesion_anterior(d):
    d = pd.Timestamp(d).normalize()
    for k in range(1, 15):
        c = d - pd.Timedelta(days=k)
        if es_sesion(c):
            return c
    return d - pd.Timedelta(days=1)


def mensaje(verdes, sin_dato, todos, atrasado, esperado, prueba=False):
    L = []
    if prueba:
        L.append("<b>PRUEBA del aviso de semaforos (no es senal)</b>")
    if len(verdes) >= 2:
        L.append("<b>LUZ VERDE DOBLE: %s</b>" % esc(" + ".join(s["nombre"] for s in verdes)))
        ap = set(s.get("apuesta") for s in verdes)
        if len(ap) == 1 and None not in ap:
            L.append("Ojo: es la misma apuesta (%s); se mueven casi a la par. Operar las dos suma riesgo." % esc(ap.pop()))
        L.append("")
    for s in verdes:
        fmt = s["formato"]
        L.append("\U0001F7E2 <b>LUZ VERDE: %s</b>" % esc(s["nombre"]))
        L.append("Cierre oficial %s: %s %s (%s en verde)."
                 % (semaforos.fecha_corta(s["fecha_cierre"]), esc(s["variable"]),
                    semaforos.txt_valor(fmt, s["valor"]),
                    "1 sesion" if s["racha"] == 1 else "%d sesiones" % s["racha"]))
        L.append("Entrada: " + esc(s["accion_si"]))
        L.append("Salida: " + esc(s["salida"]))
        L.append("")
    for s in sin_dato:
        L.append("\u26A0\uFE0F <b>%s: SIN DATO</b>. %s Hoy no se puede evaluar la regla."
                 % (esc(s["nombre"]), esc(s.get("motivo", ""))))
        L.append("")
    if prueba and not verdes and not sin_dato:
        for s in todos:
            L.append("%s: %s (cierre %s: %s %s)." % (esc(s["nombre"]), "VERDE" if s.get("estado") == "SI" else "no",
                                                    semaforos.fecha_corta(s.get("fecha_cierre")), esc(s["variable"]),
                                                    semaforos.txt_valor(s["formato"], s.get("valor"))))
    if atrasado:
        L.append("OJO: el ultimo cierre oficial no es el de la sesion anterior (%s): el dato puede estar atrasado."
                 % esperado.strftime("%d/%m"))
    L.append('<a href="%s">Abrir panel</a>' % URL_WEB)
    return "\n".join(L)


def enviar_con_reintentos(txt, enviar=None, esperas=ESPERAS_REINTENTO, dormir=time.sleep):
    enviar = enviar or estado.avisar
    for k, espera in enumerate(esperas, 1):
        if espera:
            dormir(espera)
        if enviar(txt):
            return k
        print("  intento %d de %d fallido" % (k, len(esperas)))
    return 0


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
    # atrasado: por la fecha del cierre que evalua cada semaforo (no por la ultima fila de la serie)
    atrasado = any(pd.Timestamp(s["fecha_cierre"]).normalize() < esperado for s in todos if s.get("fecha_cierre"))
    verdes = [s for s in todos if s.get("estado") == "SI"]
    sin_dato = [s for s in todos if s.get("estado") == "SIN DATO"]
    for s in todos:
        print("%s: %s (cierre %s, %s %s)%s" % (s["nombre"], s.get("estado"), s.get("fecha_cierre"), s["variable"],
                                               semaforos.txt_valor(s["formato"], s.get("valor")),
                                               (" | " + s["motivo"]) if s.get("motivo") else ""))
    if not verdes and not sin_dato and not prueba:
        print("Ningun semaforo en verde ni sin dato: no se envia nada.")
        return
    txt = mensaje(verdes, sin_dato, todos, atrasado, esperado, prueba)
    if "--dry-run" in args:
        print("--dry-run, mensaje:\n" + txt.encode("ascii", "replace").decode("ascii"))
        return
    k = enviar_con_reintentos(txt)
    if k:
        print("Aviso enviado al intento %d (%d en verde, %d sin dato%s)."
              % (k, len(verdes), len(sin_dato), ", PRUEBA" if prueba else ""))
    else:
        print("FALLO: el aviso NO se envio tras %d intentos." % len(ESPERAS_REINTENTO))
        sys.exit(1)


if __name__ == "__main__":
    main()
