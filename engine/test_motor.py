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
