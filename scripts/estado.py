"""
estado.py -- el CONTRATO DE FALLO del sistema VIX_CURVE.

POR QUE EXISTE
Las dos auditorias del 2026-09-21 coincidieron en el mismo diagnostico de fondo:
"ninguna ruta de fallo produce un codigo de salida distinto de cero ni un fichero
de estado; todo se degrada hacia un CSV con buena pinta". Es exactamente la
enfermedad que mato al Google Sheet anterior, que estuvo 3 meses congelado sin
que nadie se enterara. Este modulo es la cura: un solo sitio donde se decide que
significa fallar.

REGLA (decidida con el usuario el 2026-09-21):
  - Si falla algo que impide producir un dato CORRECTO -> se para todo, no se
    escribe el entregable, se avisa por Telegram y se deja constancia en disco.
  - El aviso NUNCA es la unica huella: si la red esta caida, el Telegram tampoco
    sale. Por eso el fichero de estado se escribe SIEMPRE y ANTES de intentar
    avisar (un vigilante que muere por la misma causa que el vigilado no es
    redundancia).

Reglas tecnicas del proyecto: ASCII, cp1252.
"""

import os
import io
import sys
import json
import datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
ESTADO = os.path.join(HERE, "data", "estado.json")
CONFIG_TG = os.path.join(HERE, "telegram_config.json")


def _credenciales():
    tok = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if tok and chat:
        return tok, chat
    if os.path.exists(CONFIG_TG):
        try:
            with io.open(CONFIG_TG, encoding="utf-8") as fh:
                c = json.load(fh)
            return c.get("token"), c.get("chat_id")
        except Exception:
            pass
    return None, None


def avisar(texto):
    """Manda un aviso por Telegram. Devuelve True/False, NUNCA lanza excepcion:
    el aviso es best-effort; la huella obligatoria es el fichero de estado."""
    import urllib.request
    tok, chat = _credenciales()
    if not tok or not chat:
        print("AVISO NO ENVIADO: sin credenciales de Telegram")
        return False
    try:
        payload = json.dumps({"chat_id": chat, "text": texto,
                              "parse_mode": "HTML"}).encode("utf-8")
        req = urllib.request.Request(
            "https://api.telegram.org/bot%s/sendMessage" % tok,
            data=payload, headers={"Content-Type": "application/json"})
        r = json.loads(urllib.request.urlopen(req, timeout=30).read().decode())
        return bool(r.get("ok"))
    except Exception as e:
        print("AVISO NO ENVIADO: %s" % str(e)[:150])
        return False


def escribir(ok, etapa, detalle="", extra=None):
    """Escribe data/estado.json de forma ATOMICA (tmp + os.replace).

    Conserva `ultima_ok` de la corrida buena anterior, para que se pueda ver de un
    vistazo cuanto lleva el sistema sin producir nada valido."""
    prev = {}
    if os.path.exists(ESTADO):
        try:
            with io.open(ESTADO, encoding="utf-8") as fh:
                prev = json.load(fh)
        except Exception:
            prev = {}
    ahora = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # Y la MISMA marca en UTC. Motivo: el vigilante corre en GitHub, cuyo reloj
    # va en UTC; comparando contra la hora local de Madrid la antiguedad salia
    # 2 horas corta (y en la primera prueba, directamente NEGATIVA). La hora
    # local se conserva porque es la que se lee de un vistazo desde aqui.
    ahora_utc = dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    d = {
        "ok": bool(ok),
        "etapa": etapa,
        "detalle": detalle,
        "momento": ahora,
        "momento_utc": ahora_utc,
        "ultima_ok": ahora if ok else prev.get("ultima_ok"),
        "ultima_ok_utc": ahora_utc if ok else prev.get("ultima_ok_utc"),
    }
    if extra:
        d.update(extra)
    os.makedirs(os.path.dirname(ESTADO), exist_ok=True)
    tmp = ESTADO + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as fh:
        json.dump(d, fh, indent=2, ensure_ascii=True)
    os.replace(tmp, ESTADO)
    return d


def fallar(etapa, detalle, extra=None):
    """Parada dura: deja constancia en disco, intenta avisar, y sale con codigo 1.

    El ORDEN importa y no se cambia: primero el disco (que no depende de la red),
    despues el aviso. Si se invierte y la red esta caida, no queda huella."""
    escribir(False, etapa, detalle, extra)
    texto = ("<b>VIX CURVE - PARADA</b>\n\n"
             "Etapa: <b>%s</b>\n%s\n\n"
             "No se ha escrito ningun entregable. Estado en data/estado.json."
             % (etapa, detalle))
    enviado = avisar(texto)
    print("PARADA en '%s': %s" % (etapa, detalle))
    print("  estado escrito en %s | aviso Telegram: %s"
          % (ESTADO, "enviado" if enviado else "NO ENVIADO"))
    sys.exit(1)


def leer():
    if not os.path.exists(ESTADO):
        return None
    try:
        with io.open(ESTADO, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None
