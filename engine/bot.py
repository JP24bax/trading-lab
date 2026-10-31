"""Bot de consola con cálculo de métricas avanzadas, persistencia de sesión y parada suave/inmediata."""
import argparse
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pandas as pd

import moneda
from ai_filter import PROVIDERS, ai_veto
import backtest as bt
import filtros as flt
from backtest import check_strategy, search
from broker import IQBroker, SimBroker
from strategies import STRATEGIES

HERE = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))
# En Railway/Render el disco se borra en cada despliegue: apunta TRADINGLAB_DATA_DIR a un volumen persistente
# para no perder el historial (hacen falta 100-200 operaciones para evaluar una estrategia).
DATA_DIR = os.getenv("TRADINGLAB_DATA_DIR") or HERE
STATE_FILE = os.path.join(DATA_DIR, "session_state.json")

STOP = threading.Event()
STOP_SOFT = threading.Event()
ON_EVENT = lambda kind, **data: None


def save_session_state(config, trades):
    try:
        cfg = dict(vars(config)) if isinstance(config, SimpleNamespace) else dict(config)
        for secret in ("password", "email"):
            cfg.pop(secret, None)
        data = {
            "config": cfg,
            "trades": trades,
            "timestamp": time.time()
        }
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"Error guardando sesión: {e}")


def load_session_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return None


def compute_metrics(trades_list):
    if not trades_list:
        return {
            "total": 0, "wins": 0, "winrate": 0.0, "pnl": 0.0,
            "ev": 0.0, "max_drawdown": 0.0, "best_streak": 0, "worst_streak": 0
        }

    df = pd.DataFrame(trades_list)
    total = len(df)
    wins = int(df["won"].sum())
    winrate = wins / total if total > 0 else 0.0
    pnl = float(df["profit"].sum())
    ev = pnl / total if total > 0 else 0.0

    cumulative = df["profit"].cumsum()
    peak = np.maximum.accumulate(cumulative)
    drawdowns = peak - cumulative
    max_drawdown = float(drawdowns.max()) if len(drawdowns) > 0 else 0.0

    best_streak = worst_streak = cur_win = cur_loss = 0
    for won in df["won"]:
        if won:
            cur_win += 1
            cur_loss = 0
            best_streak = max(best_streak, cur_win)
        else:
            cur_loss += 1
            cur_win = 0
            worst_streak = max(worst_streak, cur_loss)

    return {
        "total": total, "wins": wins, "winrate": winrate, "pnl": pnl,
        "ev": ev, "max_drawdown": max_drawdown,
        "best_streak": best_streak, "worst_streak": worst_streak,
    }


def load_login():
    if os.getenv("IQ_EMAIL") and os.getenv("IQ_PASSWORD"):
        return os.getenv("IQ_EMAIL"), os.getenv("IQ_PASSWORD")
    p = os.path.join(HERE, "login.txt")
    if os.path.exists(p):
        lines = [l.strip() for l in open(p, encoding="utf-8") if l.strip()]
        if len(lines) >= 2:
            return lines[0], lines[1]
    return os.getenv("IQ_EMAIL"), os.getenv("IQ_PASSWORD")


def load_tickers():
    p = os.path.join(HERE, "tickers.txt")
    if os.path.exists(p):
        return [l.strip().upper() for l in open(p) if l.strip() and not l.startswith("#")]
    return ["EURUSD"]


def make_broker(sim, on_status=None, email=None, password=None):
    """Si se pasan email/password (los de la interfaz) se usan; si no, login.txt o variables de entorno."""
    if sim:
        b = SimBroker()
    else:
        if not (email and password):
            email, password = load_login()
        if not email or not password:
            raise RuntimeError("Falta el login: escribe correo y contraseña en la app o crea login.txt")
        b = IQBroker(email, password, "PRACTICE")
    b.connect(on_status=on_status)
    return b


def broker_from_args(a):
    return make_broker(a.sim, on_status=lambda state, msg: ON_EVENT("conn_status", state=state, msg=msg),
                       email=getattr(a, "email", None), password=getattr(a, "password", None))


def money_setup(b, a):
    cur = b.currency() or "USD"
    k = moneda.cop_per_unit(cur, getattr(a, "cop_rate", None))
    amount_acct = round(a.amount / k, 2)
    return k, cur, amount_acct


def cmd_backtest(a):
    b = broker_from_args(a)
    for asset in a.assets:
        if STOP.is_set(): return
        pay = b.get_payout(asset)
        df = b.candles_history(asset, 60, a.history)
        name, table, info = search(df, expiry_candles=a.expiry, payout=pay, cfg_filtros=cfg_filtros_de(a))
        ON_EVENT("table", asset=asset, rows=bt._num(table.round(3).to_dict("records")), name=name,
                 info=bt._num(info), payout=pay, candles=len(df))


def cmd_test_order(a):
    asset = a.assets[0]
    b = broker_from_args(a)
    k, cur, amount_acct = money_setup(b, a)
    bal0 = b.get_balance()
    ON_EVENT("balance", value=bal0 * k)
    try:
        order = b.buy(asset, amount_acct, 1, a.expiry)
    except Exception as e:
        ON_EVENT("log", text=f"✖ FALLÓ orden de prueba: {e}")
        return
    if not a.sim:
        time.sleep(a.expiry * 60 + 2)
    won, profit = b.result(order)
    bal1 = b.get_balance()
    ON_EVENT("balance", value=bal1 * k)


def cfg_filtros_de(a):
    """Config de filtros a partir de los argumentos de la sesión (todo opcional, con valores por defecto)."""
    return dict(
        volatilidad=bool(getattr(a, "filtro_volatilidad", True)),
        atr_min=float(getattr(a, "atr_min", flt.FILTROS_DEFECTO["atr_min"])),
        atr_max=float(getattr(a, "atr_max", flt.FILTROS_DEFECTO["atr_max"])),
        tendencia=bool(getattr(a, "filtro_tendencia", True)),
        horario=None,   # el horario se aplica en vivo con start_hour/end_hour (hora Colombia)
    )


def _ranking_estrategias(b, a):
    """Estrategias a usar. Con 'auto' se elige con walk-forward sobre histórico y se avisa si NO hay evidencia."""
    if a.strategy != "auto":
        return [a.strategy]
    try:
        asset0 = a.assets[0]
        pay, fuente = b.get_payout_detail(asset0)
        df_h = b.candles_history(asset0, 60, a.history)
        pick, top, info = search(df_h, expiry_candles=a.expiry, payout=pay, cfg_filtros=cfg_filtros_de(a))
        texto, nivel = info.get("veredicto", ("", "warn"))
        if getattr(b, "synthetic", False):
            ON_EVENT("log", text="⚠ Modo simulación: el histórico es aleatorio, la elección automática no tiene valor real.")
        else:
            icono = {"good": "✔", "warn": "⚠", "bad": "✖"}.get(nivel, "•")
            ON_EVENT("log", text=f"{icono} Validación del método automático (walk-forward, payout {pay:.0%} {fuente}): {texto}")
        ranking = [pick] + [s for s in top["strategy"].tolist() if s != pick and s in STRATEGIES] if len(top) else [pick]
        ranking = [r for r in ranking if r in STRATEGIES]
        if ranking:
            ON_EVENT("log", text=f"📊 Estrategia elegida (auto): {ranking[0]}. Candidatas: {', '.join(ranking[:5])}")
            return ranking
    except Exception as e:
        ON_EVENT("log", text=f"⚠ No se pudo calcular la elección automática ({e}); se usa rsi14_30_70")
    return ["rsi14_30_70"]


def _importe(a, balance_cuenta, amount_fijo):
    """Importe de la operación, en la moneda de la cuenta. Con risk_pct>0 es ese % del saldo ACTUAL (si el saldo baja,
    el importe baja; nunca se aumenta tras perder). Con risk_pct=0 se usa el importe fijo configurado."""
    pct = float(getattr(a, "risk_pct", 0) or 0)
    if pct > 0:
        return max(1.0, round(balance_cuenta * pct / 100, 2))   # 1 = importe mínimo habitual del bróker
    return amount_fijo


def cmd_run(a):
    b = broker_from_args(a)
    k, cur, amount_acct = money_setup(b, a)
    es_sim = bool(getattr(b, "synthetic", False))

    start_bal = b.get_balance()
    prev = load_session_state()
    trades_list = list(prev.get("trades", [])) if prev else []   # historial acumulado entre sesiones
    trades = wins = loss_streak = 0                                # contadores de ESTA sesión
    last_t = {}
    bloqueos = {}                                                  # conteo de señales descartadas por cada filtro

    cfg_f = cfg_filtros_de(a)
    payout_min = float(getattr(a, "payout_minimo", 0.80))
    tz = int(getattr(a, "tz_offset", flt.TZ_COLOMBIA))
    rotar = bool(getattr(a, "rotar_estrategia", False)) and a.strategy == "auto"
    cache_payout = {}                                              # asset -> (instante, payout)

    ranking = _ranking_estrategias(b, a)
    idx_estrategia = 0

    def payout_vigente(asset):
        t0, val = cache_payout.get(asset, (0.0, None))
        if val is None or time.time() - t0 > 60:
            val = b.get_payout(asset)
            cache_payout[asset] = (time.time(), val)
        return val

    def bloquear(motivo_corto, asset, detalle):
        bloqueos[motivo_corto] = bloqueos.get(motivo_corto, 0) + 1
        ON_EVENT("log", text=f"🚫 {asset}: señal descartada, {detalle}")

    ON_EVENT("balance", value=start_bal * k)
    ON_EVENT("log", text=f"✔ Conexión exitosa. Saldo disponible: {moneda.fmt(start_bal * k)}")
    if es_sim:
        ON_EVENT("log", text="⚠ SIMULACIÓN: cada operación se resuelve al azar (50 %). Estos resultados NO miden ninguna estrategia.")

    try:
        while not STOP.is_set():
            if STOP_SOFT.is_set():
                ON_EVENT("log", text="⚠ Parada suave: Finalizando tras operaciones activas.")
                break

            ahora_utc = datetime.now(timezone.utc)
            hora_col = flt.hora_local(ahora_utc, tz).hour
            start_h = getattr(a, "start_hour", 0)
            end_h = getattr(a, "end_hour", 24)
            if not flt.en_horario(hora_col, start_h, end_h):
                ON_EVENT("badge_status", state="PAUSED_LIMIT", text="Fuera de Horario")
                time.sleep(5)
                continue

            for asset in a.assets:
                if STOP.is_set(): break

                if not es_sim and not flt.mercado_abierto(asset, ahora_utc):
                    ON_EVENT("badge_status", state="PAUSED_LIMIT", text="Mercado cerrado")
                    continue

                try:
                    df = b.candles(asset, 60, 200)
                except Exception as e:
                    ON_EVENT("log", text=f"⚠ Error leyendo velas de {asset}: {e}")
                    continue

                if "t" in df:
                    t = int(df["t"].iloc[-1])
                    if last_t.get(asset) == t: continue
                    last_t[asset] = t

                strat_name = ranking[idx_estrategia]
                sig = int(STRATEGIES[strat_name](df).iloc[-1])

                ON_EVENT("log", text=f"🔍 Escaneando {asset} con estrategia {strat_name}... (Señal: {sig})")

                if sig == 0: continue

                # ── Filtros de contexto ─────────────────────────────────────
                try:
                    payout_now = payout_vigente(asset)
                except Exception:
                    payout_now = 0.80
                if payout_now < payout_min:
                    bloquear("payout", asset, f"payout {payout_now:.0%} < mínimo {payout_min:.0%} "
                                              f"(equilibrio {bt.breakeven(payout_now):.1%})")
                    continue

                df_tf = None
                if cfg_f["tendencia"]:
                    try:
                        df_tf = b.candles(asset, flt.TF_SUPERIOR_SEG, 120)
                    except Exception:
                        df_tf = None            # sin datos de 15 min el filtro no bloquea (se registra tendencia 0)
                permitido, motivo, ctx = flt.evaluar_en_vivo(df, sig, cfg_f, df_tf)
                if not permitido:
                    bloquear(motivo.split(" (")[0], asset, motivo)
                    continue

                provider = getattr(a, "ai", "none")
                ok, why = ai_veto(df, sig, asset, provider, strat_name) if provider != "none" else (True, "sin IA")
                # La IA cuenta como usada solo si realmente respondió (no si falló o no hay clave)
                ai_used = provider if (provider != "none" and not why.startswith(("IA no disponible", "IA desactivada"))) else "none"
                if not ok:
                    ON_EVENT("log", text=f"🛡 Filtro IA vetó la operación en {asset}: {why}")
                    ON_EVENT("ai_veto", asset=asset, direction=sig, reason=why)
                    continue

                # ── Importe: % del saldo, y SOLO se reduce tras rachas en contra (nunca martingala) ──
                current_amount = _importe(a, b.get_balance(), amount_acct)
                if loss_streak >= getattr(a, "risk_streak_trigger", 2):
                    reduction_pct = getattr(a, "risk_reduction_pct", 50)
                    current_amount = max(1.0, round(current_amount * (1 - reduction_pct / 100), 2))
                    ON_EVENT("log", text=f"📉 Riesgo reducido por racha en contra: {current_amount} {cur}")

                ON_EVENT("badge_status", state="TRADE_IN_PROGRESS", text="Operación en Curso")
                try:
                    order = b.buy(asset, current_amount, sig, a.expiry)
                    ON_EVENT("log", text=f"🚀 Orden enviada en {asset} ({'CALL' if sig==1 else 'PUT'}) por {current_amount} {cur}. Esperando cierre...")
                except Exception as e:
                    ON_EVENT("log", text=f"🚨 ALERTA: Orden pausada ({e})")
                    ON_EVENT("conn_status", state="DISCONNECTED", msg="Orden rechazada")
                    return

                if not a.sim: time.sleep(a.expiry * 60 + 2)

                try:
                    won, profit = b.result(order)
                except Exception as e:
                    ON_EVENT("log", text=f"🚨 ALERTA CRÍTICA: Estado incierto ({e})")
                    return

                trades += 1
                wins += bool(won)
                loss_streak = 0 if won else loss_streak + 1
                bal = b.get_balance()
                pnl = (bal - start_bal) * k

                trade_data = {
                    "asset": asset, "strategy": strat_name, "direction": sig,
                    "won": bool(won), "profit": profit * k, "trades": trades, "ai": ai_used,
                    "wins": wins, "pnl": pnl, "balance": bal * k,
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "indicators": {"price": float(df["close"].iloc[-1]), "reason": why},
                    "snapshot": df.tail(15).to_dict(orient="records"),
                    # Contexto para analizar después en qué condiciones se gana o se pierde:
                    "sim": es_sim, "payout": float(payout_now), "expiry": int(a.expiry),
                    "contexto": {**ctx, "hora_col": hora_col, "dia_semana": ahora_utc.weekday(),
                                 "importe": current_amount},
                }

                trades_list.append(trade_data)
                save_session_state(a, trades_list)
                ON_EVENT("trade", **trade_data)
                ON_EVENT("badge_status", state="WAITING", text="Analizando Mercado")

                # Rotación (opt-in): cambiar de estrategia tras perder es el sesgo del jugador, por eso está
                # desactivada por defecto. Se evalúa una vez por operación, tras cada pérdida par seguida.
                if rotar and len(ranking) > 1 and loss_streak >= 2 and loss_streak % 2 == 0:
                    idx_estrategia = (idx_estrategia + 1) % len(ranking)
                    ON_EVENT("log", text=f"🔄 Cambio de estrategia a {ranking[idx_estrategia]}")

                if pnl <= -a.max_loss or loss_streak >= a.max_streak or trades >= a.max_trades:
                    ON_EVENT("log", text="🛑 Límite de sesión o pérdidas alcanzado."
                                         + (f" Señales descartadas por filtros: {bloqueos}" if bloqueos else ""))
                    ON_EVENT("badge_status", state="PAUSED_LIMIT", text="Pausado por Límite")
                    return

            time.sleep(1)

    except Exception as e:
        ON_EVENT("log", text=f"✖ Error en ejecución: {e}")
