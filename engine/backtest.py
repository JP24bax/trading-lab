"""Backtest con búsqueda masiva, simulación de fricciones de mercado (spread, slippage, rechazos)
y cálculo de métricas avanzadas (Profit Factor, Máxima racha negativa)."""
import math
import numpy as np
import pandas as pd

from strategies import STRATEGIES


def breakeven(payout):
    """Win rate mínima para no perder dinero. payout=0.85 -> 54.05 %."""
    return 1 / (1 + payout)


def _empty_result():
    return dict(n=0, wins=0, losses=0, winrate=0.0, pnl=0.0, z=0.0, ev=0.0,
                profit_factor=0.0, max_loss_streak=0, equity_curve=[])


def evaluate(df, signal, expiry_candles=1, payout=0.85, spread_pips=0.00002, slippage_prob=0.05, rejection_rate=0.02):
    """Evalúa la estrategia incorporando condiciones reales de mercado:
    - Spread: en opciones binarias se gana por acertar la dirección, así que el spread real es mínimo
      (por defecto 0.2 pips). Un valor mayor castiga cada operación como si fuera forex y distorsiona el win rate.
    - Slippage: probabilidad de que una operación ganadora se convierta en perdedora por mal precio de entrada.
    - Rechazo de órdenes: porcentaje de señales que el bróker no ejecuta.
    """
    fut = df["close"].shift(-expiry_candles)
    
    # Simular reyección aleatoria de órdenes
    rng = np.random.default_rng(42)
    rejected_mask = rng.random(len(signal)) < rejection_rate
    effective_signal = signal.copy()
    effective_signal[rejected_mask] = 0

    # Determinar dirección de movimiento aplicando un spread leve
    price_diff = fut - df["close"]
    # Para CALL (1), la diferencia debe superar el spread. Para PUT (-1), la caída debe superar el spread.
    adjusted_diff = np.where(effective_signal == 1, price_diff - spread_pips,
                     np.where(effective_signal == -1, price_diff + spread_pips, price_diff))
    
    move = np.sign(adjusted_diff)
    mask = (effective_signal != 0) & fut.notna()
    s, m = effective_signal[mask], move[mask]
    n = int(mask.sum())

    if n == 0:
        return _empty_result()

    # Aplicar probabilidad de slippage adverso puntual
    slippage_mask = rng.random(n) < slippage_prob
    
    wins_mask = (s == m) & (m != 0) & (~slippage_mask)
    wins = int(wins_mask.sum())
    ties = int((m == 0).sum())
    losses = n - wins - ties

    # Cálculo de PnL y Curva de Equity
    pnl = wins * payout - losses
    gross_profit = wins * payout
    gross_loss = float(losses)
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (gross_profit if gross_profit > 0 else 0.0)

    # Curva de Equity y racha negativa
    trades_outcomes = np.where(wins_mask, payout, -1.0)
    equity_curve = np.cumsum(trades_outcomes).tolist()

    cur_loss = max_loss_streak = 0
    for w in wins_mask:
        if not w:
            cur_loss += 1
            max_loss_streak = max(max_loss_streak, cur_loss)
        else:
            cur_loss = 0

    decided = max(n - ties, 1)
    wr = wins / decided
    be = breakeven(payout)
    z = (wr - be) / math.sqrt(be * (1 - be) / decided) if decided > 1 else 0.0

    return dict(
        n=n, wins=wins, losses=losses, winrate=wr, pnl=pnl, z=z, ev=pnl / n,
        profit_factor=profit_factor, max_loss_streak=max_loss_streak, equity_curve=equity_curve
    )


def search(df, expiry_candles=1, payout=0.85, min_trades=20, min_test=15, z_min=1.2):
    if df is None or len(df) < 50:
        return "rsi14_30_70", pd.DataFrame(), {"candidates": 0, "tested": 0, "test": _empty_result()}

    n = len(df)
    a, b = int(n * 0.5), int(n * 0.75)
    parts = {"tr": (0, a), "va": (a, b), "te": (b, n)}
    rows = []

    for name, fn in STRATEGIES.items():
        try:
            sig = fn(df) if callable(fn) else fn
            if sig is None or len(sig) == 0:
                continue
        except Exception:
            continue

        r = {"strategy": name}
        for k, (i, j) in parts.items():
            if k == "te":
                continue
            e = evaluate(df.iloc[i:j], sig.iloc[i:j], expiry_candles, payout)
            r[f"{k}_n"], r[f"{k}_wr"], r[f"{k}_ev"], r[f"{k}_z"] = e["n"], e["winrate"], e["ev"], e["z"]
        rows.append(r)

    if not rows:
        return "rsi14_30_70", pd.DataFrame(), {"candidates": 0, "tested": 0, "test": _empty_result()}

    t = pd.DataFrame(rows)
    t["score"] = np.minimum(t["tr_wr"], t["va_wr"])
    
    ok = t[(t["tr_n"] >= min_trades) & (t["va_n"] >= max(5, min_trades // 2))]
    top = (ok if len(ok) > 0 else t).sort_values("score", ascending=False).head(10).reset_index(drop=True)

    info = {"candidates": len(ok), "tested": len(t)}
    pick = top.iloc[0]["strategy"] if len(top) > 0 else "rsi14_30_70"
    
    i, j = parts["te"]
    try:
        sig_pick = STRATEGIES[pick](df) if callable(STRATEGIES[pick]) else STRATEGIES[pick]
        te = evaluate(df.iloc[i:j], sig_pick.iloc[i:j], expiry_candles, payout)
        info.update(pick=pick, test=te)
    except Exception:
        info.update(pick=pick, test=_empty_result())

    return pick, top, info


def check_strategy(df, name, expiry_candles=1, payout=0.85, min_test=30, z_min=1.64):
    n = len(df)
    b = int(n * 0.75)
    te = evaluate(df.iloc[b:], STRATEGIES[name](df).iloc[b:], expiry_candles, payout)
    return te, (te["n"] >= min_test and te["ev"] > 0 and te["z"] > z_min)


def verdict(res, payout=0.85, min_trades=30, z_min=1.64):
    """Juicio honesto sobre un resultado fuera de muestra. Devuelve (texto, nivel) con nivel good/warn/bad.
    Debe aplicarse SOLO a datos que no se usaron para elegir la estrategia."""
    be = breakeven(payout)
    n = res.get("n", 0)
    if n < min_trades:
        return (f"Muestra muy pequeña ({n} operaciones, se piden al menos {min_trades}): "
                f"el resultado no es concluyente.", "warn")
    if res["winrate"] <= be or res["ev"] <= 0:
        return (f"Sin ventaja: el win rate ({res['winrate']:.1%}) no supera el punto de equilibrio "
                f"({be:.1%}) en datos no vistos.", "bad")
    if res["z"] < z_min:
        return (f"Ventaja no demostrada: supera el equilibrio ({be:.1%}) pero z={res['z']:.2f} "
                f"(<{z_min}); puede ser azar.", "warn")
    return (f"Ventaja estadísticamente apoyada en datos no vistos (z={res['z']:.2f}). "
            f"Confírmala en cuenta demo antes de arriesgar dinero real.", "good")
