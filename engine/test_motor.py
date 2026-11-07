"""Pruebas del motor. Ejecutar desde engine/:  python test_motor.py   (o: python -m pytest test_motor.py)

Cubren lo que más fácil se rompe en silencio: mirar al futuro (look-ahead), falsos positivos por comparaciones
múltiples, calendario/horarios, tamaño de las operaciones y la puerta de la cuenta demo."""
import math
import os
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analisis
import backtest as bt
import filtros as flt


def serie(n, seed, phi=0.0, sigma=0.0004, t0=1_760_000_000):
    """Serie sintética de velas de 1 min. phi<0 mete reversión a la media (una ventaja real y pequeña)."""
    rng = np.random.default_rng(seed)
    e = rng.normal(0, sigma, n)
    r = np.zeros(n)
    for i in range(1, n):
        r[i] = phi * r[i - 1] + e[i]
    c = 1.10 * np.exp(np.cumsum(r))
    o = np.r_[1.10, c[:-1]]
    h = np.maximum(o, c) + abs(rng.normal(0, sigma / 4, n))
    l = np.minimum(o, c) - abs(rng.normal(0, sigma / 4, n))
    return pd.DataFrame(dict(open=o, high=h, low=l, close=c, t=(t0 // 60 * 60) + np.arange(n) * 60))


# ── Estadística ─────────────────────────────────────────────────────────────
def test_breakeven_y_p_value():
    assert abs(bt.breakeven(0.85) - 0.5405) < 1e-4
    assert abs(bt.p_value(0.0) - 0.5) < 1e-9
    assert abs(bt.p_value(1.645) - 0.05) < 1e-3


def test_holm():
    aj = bt.holm([0.01, 0.04, 0.03, 0.005])
    assert np.allclose(aj, [0.03, 0.06, 0.06, 0.02])          # ejemplo clásico de Holm
    assert all(0 <= x <= 1 for x in bt.holm([0.9, 0.99, 0.5]))


def test_empate_no_cuenta_como_perdida():
    df = pd.DataFrame({"close": [1.0, 1.0, 1.1, 1.0]})
    res, op = bt.resultados(df, np.array([1, 1, -1, 0]), expiry_candles=1)
    # vela0: CALL, sube 0 -> empate | vela1: CALL, sube -> gana | vela2: PUT, baja -> gana | vela3: sin futuro
    assert list(res[:3]) == [0, 1, 1] and list(op) == [True, True, True, False]
    st = bt.estadisticas(res, op, 0.85)
    assert st["ties"] == 1 and st["losses"] == 0 and abs(st["pnl"] - 1.7) < 1e-9


# ── Sin mirar al futuro ─────────────────────────────────────────────────────
def test_filtros_son_causales():
    df = serie(3000, 7)
    completo_t = flt.tendencia_desde_1m(df)
    completo_r = flt.atr_ratio(df).to_numpy()
    for k in (1200, 1777, 2500):
        parcial_t = flt.tendencia_desde_1m(df.iloc[:k])
        parcial_r = flt.atr_ratio(df.iloc[:k]).to_numpy()
        assert np.array_equal(completo_t[:k], parcial_t), "la tendencia de 15m usa datos futuros"
        assert np.allclose(completo_r[:k], parcial_r, equal_nan=True), "el ATR usa datos futuros"


def test_ventanas_walk_forward_sin_solape():
    for i, j, m in bt._ventanas(5000, 1500, 500):
        assert i < j < m and (m - j) == 500 and (j - i) == 1500
    tests = [(j, m) for _, j, m in bt._ventanas(5000, 1500, 500)]
    assert all(tests[k][1] <= tests[k + 1][0] for k in range(len(tests) - 1)), "las ventanas de prueba se solapan"


def test_ruido_no_da_ventaja_demostrada():
    """En ruido puro el veredicto no puede ser 'good' (con 12 series la probabilidad de un falso positivo es baja
    por diseño: el p-valor se ajusta por las 92 estrategias y se exige validación fuera de muestra)."""
    for seed in range(40, 52):
        a = bt.analisis_completo(serie(5000, seed), 1, 0.85, flt.FILTROS_DEFECTO, comparar=False)
        assert a["veredicto"][1] != "good", f"falso positivo con la semilla {seed}"
        if len(a["top"]):
            assert a["top"].iloc[0]["p_ajustado"] >= 0.05, f"Holm declaró significativa una serie aleatoria ({seed})"


def test_ventaja_plantada_se_detecta():
    """Con reversión a la media real, el walk-forward debe superar el equilibrio fuera de muestra."""
    wr = []
    for seed in range(500, 506):
        a = bt.analisis_completo(serie(5000, seed, phi=-0.2), 1, 0.85, None, comparar=False)
        wr.append(a["walk"]["oos"]["winrate"])
    assert np.mean(wr) > 0.55, f"no detecta una ventaja real (win rate OOS medio {np.mean(wr):.3f})"


def test_sintetico_siempre_advierte():
    res = {"n": 500, "winrate": 0.7, "ev": 0.2, "p": 0.0001, "z": 5}
    assert bt.verdict(res, 0.85, sintetico=True)[1] == "warn"


# ── Calendario y horarios ───────────────────────────────────────────────────
def _utc(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


def test_mercado_abierto():
    assert flt.mercado_abierto("EURUSD", _utc(2026, 10, 28, 15))           # miércoles
    assert not flt.mercado_abierto("EURUSD", _utc(2026, 10, 31, 15))       # sábado
    assert not flt.mercado_abierto("EURUSD", _utc(2026, 10, 30, 22, 30))   # viernes tarde
    assert not flt.mercado_abierto("EURUSD", _utc(2026, 11, 1, 21, 59))    # domingo antes de abrir
    assert flt.mercado_abierto("EURUSD", _utc(2026, 11, 1, 22, 0))         # domingo apertura
    assert flt.mercado_abierto("EURUSD-OTC", _utc(2026, 10, 31, 15))       # OTC 24/7


def test_en_horario():
    assert flt.en_horario(3, 0, 24) and flt.en_horario(8, 8, 16) and not flt.en_horario(16, 8, 16)
    assert flt.en_horario(23, 22, 6) and flt.en_horario(5, 22, 6) and not flt.en_horario(12, 22, 6)


def test_hora_colombia():
    assert flt.hora_local(_utc(2026, 10, 31, 16), -5).hour == 11


# ── Importe ─────────────────────────────────────────────────────────────────
def test_importe_porcentaje_y_nunca_sube_tras_perder():
    from types import SimpleNamespace
    import bot
    a = SimpleNamespace(risk_pct=1.0)
    saldo, previo, ganada_ant = 1000.0, None, True
    for gana in (False, False, False, True, False, False):
        imp = bot._importe(a, saldo, 99.0)
        assert abs(imp - max(1.0, round(saldo * 0.01, 2))) < 1e-9
        if previo is not None and not ganada_ant:
            assert imp <= previo + 1e-9, "el importe subió tras una pérdida (martingala)"
        previo, ganada_ant = imp, gana
        saldo += imp * 0.85 if gana else -imp
    assert bot._importe(SimpleNamespace(risk_pct=0), 1000.0, 7.5) == 7.5      # 0 = importe fijo


# ── Puerta de la demo ───────────────────────────────────────────────────────
def _trades(n, wr, sim=False, estrategia="rsi14_30_70", payout=0.85):
    rng = np.random.default_rng(1)
    gana = np.zeros(n, dtype=bool)
    gana[:int(round(n * wr))] = True
    rng.shuffle(gana)
    return [{"won": bool(g), "sim": sim, "payout": payout, "strategy": estrategia, "asset": "EURUSD", "direction": 1}
            for g in gana]


def test_puerta_demo():
    v = analisis.veredicto_demo(_trades(500, 0.9, sim=True))
    assert v["contadas"] == 0 and v["nivel"] == "warn", "las operaciones simuladas no deben contar"
    v = analisis.veredicto_demo([{**t, "sim": None} for t in _trades(300, 0.9)])
    assert v["contadas"] == 0, "origen desconocido no debe contar"
    assert analisis.veredicto_demo(_trades(60, 0.8))["nivel"] == "warn"            # muestra insuficiente
    assert analisis.veredicto_demo(_trades(250, 0.50))["nivel"] == "bad"           # bajo el equilibrio
    assert analisis.veredicto_demo(_trades(250, 0.55))["nivel"] == "warn"          # supera equilibrio, p alto
    assert analisis.veredicto_demo(_trades(250, 0.66))["nivel"] == "good"          # ventaja clara
    assert analisis.veredicto_demo(_trades(120, 0.70))["nivel"] == "warn"          # prometedor pero <200


def test_grupos_pequenos_son_ruido():
    ts = _trades(150, 0.6)
    for i, t in enumerate(ts):
        t["contexto"] = {"hora_col": 9 if i < 120 else 10, "atr_ratio": 1.0, "tendencia_tf": 1}
    an = analisis.analizar_operaciones(ts)
    horas = {f["grupo"]: f for f in an["dimensiones"]["hora"]}
    assert horas["09h"]["suficiente"] and not horas["10h"]["suficiente"]
    assert horas["10h"]["lectura"].startswith("ruido")



# ── Robustez ante rechazos del bróker ───────────────────────────────────────
def _correr_bot(broker, max_trades=2, espera_max=40, parar_a_los=None):
    """Ejecuta cmd_run con un bróker de prueba y devuelve (logs, hilos_ok). No toca session_state.json real."""
    import tempfile
    import threading
    import time as _t
    from types import SimpleNamespace
    import bot
    from strategies import STRATEGIES

    logs = []
    viejo = (bot.ON_EVENT, bot.STATE_FILE, bot.broker_from_args, bot.ENFRIAMIENTO_RECHAZO_S, dict(STRATEGIES))
    bot.ON_EVENT = lambda kind, **d: logs.append(d.get("text", "")) if kind == "log" else None
    bot.STATE_FILE = os.path.join(tempfile.mkdtemp(), "estado.json")
    bot.broker_from_args = lambda a: broker
    bot.ENFRIAMIENTO_RECHAZO_S = 0
    STRATEGIES["_siempre_call"] = lambda df: pd.Series(1, index=df.index)
    a = SimpleNamespace(sim=True, assets=["EURUSD"], amount=5000, expiry=1, history=600, strategy="_siempre_call",
                        max_loss=10**9, max_streak=99, max_trades=max_trades, risk_pct=1.0,
                        filtro_volatilidad=False, filtro_tendencia=False, payout_minimo=0.5, ai="none")
    bot.STOP.clear()
    if parar_a_los:
        threading.Timer(parar_a_los, bot.STOP.set).start()
    hilo = threading.Thread(target=bot.cmd_run, args=(a,), daemon=True)
    try:
        hilo.start()
        hilo.join(espera_max)
        vivo = hilo.is_alive()
        bot.STOP.set()
        hilo.join(5)
    finally:
        bot.ON_EVENT, bot.STATE_FILE, bot.broker_from_args, bot.ENFRIAMIENTO_RECHAZO_S = viejo[:4]
        STRATEGIES.clear()
        STRATEGIES.update(viejo[4])
        bot.STOP.clear()
    return logs, not vivo


def test_un_rechazo_no_para_la_sesion():
    from broker import OrdenRechazada, SimBroker

    class Inestable(SimBroker):
        compras = 0

        def buy(self, *a, **k):
            Inestable.compras += 1
            if Inestable.compras <= 2:
                raise OrdenRechazada("Orden rechazada por el bróker. Motivo/ID: Cannot purchase an option (the asset is not available at the moment.)")
            return super().buy(*a, **k)

    logs, termino = _correr_bot(Inestable(), max_trades=2)
    assert termino, "la sesión no terminó sola"
    assert sum("rechazada" in l for l in logs) == 2, "debía registrar los 2 rechazos"
    assert sum("Orden enviada" in l for l in logs) >= 2, "tras los rechazos debía seguir operando"
    assert any("Límite de sesión" in l for l in logs), "debía llegar al límite de operaciones, no abortar por el rechazo"


def test_muchos_fallos_seguidos_detienen_la_sesion():
    from broker import OrdenRechazada, SimBroker

    class Roto(SimBroker):
        def buy(self, *a, **k):
            raise OrdenRechazada("rechazo permanente")

    logs, termino = _correr_bot(Roto(), max_trades=5)
    assert termino
    assert any("órdenes seguidas fallaron" in l for l in logs)


def test_activo_cerrado_no_envia_ordenes():
    from broker import SimBroker

    class Cerrado(SimBroker):
        intentos = 0

        def abierto(self, asset, minutes=1):
            return False

        def buy(self, *a, **k):
            Cerrado.intentos += 1
            return super().buy(*a, **k)

    logs, _ = _correr_bot(Cerrado(), max_trades=5, parar_a_los=4)
    assert Cerrado.intentos == 0, "se intentó comprar en un activo cerrado"
    assert sum("no está disponible" in l for l in logs) == 1, "el aviso debe salir una sola vez, no cada vela"


# ── Token de acceso ─────────────────────────────────────────────────────────
def test_token_de_acceso():
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect
    import web_service as ws

    viejo = os.environ.get("TRADINGLAB_TOKEN")
    try:
        os.environ.pop("TRADINGLAB_TOKEN", None)
        with TestClient(ws.app) as c:
            assert c.get("/strategies").status_code == 200, "sin token configurado todo debe seguir abierto"
            assert c.get("/health").json()["auth"] is False

        os.environ["TRADINGLAB_TOKEN"] = "secreto-123"
        with TestClient(ws.app) as c:
            assert c.get("/strategies").status_code == 401
            assert c.get("/strategies", headers={"X-TradingLab-Token": "mal"}).status_code == 401
            assert c.get("/strategies", headers={"X-TradingLab-Token": "secreto-123"}).status_code == 200
            assert c.post("/session/stop", json={"modo": "now"}).status_code == 401
            assert c.get("/session/state").status_code == 401, "las operaciones no deben leerse sin token"
            h = c.get("/health")
            assert h.status_code == 200 and h.json()["auth"] is True        # /health queda abierto
            pre = c.options("/strategies", headers={"Origin": "http://localhost:3000",
                                                    "Access-Control-Request-Method": "GET",
                                                    "Access-Control-Request-Headers": "x-tradinglab-token"})
            assert pre.status_code == 200, "el preflight CORS no debe pedir token"
            r = c.get("/strategies", headers={"Origin": "http://localhost:3000"})
            assert r.headers.get("access-control-allow-origin") == "http://localhost:3000", "el 401 debe llevar cabeceras CORS"
            try:
                with c.websocket_connect("/ws?token=mal") as w:
                    w.receive_text()
                assert False, "el WebSocket aceptó un token incorrecto"
            except WebSocketDisconnect as e:
                assert e.code == 4401
            with c.websocket_connect("/ws?token=secreto-123") as w:
                assert '"kind": "init"' in w.receive_text()
    finally:
        if viejo is None:
            os.environ.pop("TRADINGLAB_TOKEN", None)
        else:
            os.environ["TRADINGLAB_TOKEN"] = viejo

if __name__ == "__main__":
    pruebas = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    fallos = 0
    for nombre, fn in pruebas:
        try:
            fn()
            print(f"  OK   {nombre}")
        except Exception as e:                      # noqa: BLE001
            fallos += 1
            print(f"  FALLA {nombre}: {type(e).__name__}: {e}")
    print(f"\n{len(pruebas) - fallos}/{len(pruebas)} pruebas pasan")
    sys.exit(1 if fallos else 0)
