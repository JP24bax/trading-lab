"""Backtest honesto: walk-forward, p-valores ajustados por comparaciones múltiples y filtros de contexto.

Lo que se mide (y por qué):
- WALK-FORWARD: en cada ventana se elige la mejor estrategia con los datos de entrenamiento y se opera SOLO la ventana
  siguiente, que no se usó para elegir. Se juntan todas esas operaciones fuera de muestra. Eso evalúa el
  PROCEDIMIENTO completo («elegir la mejor y operarla»), que es lo que se haría en la práctica.
- COMPARACIONES MÚLTIPLES: con 90+ estrategias, la «mejor» casi siempre es suerte. El ranking muestra el p-valor
  ajustado (Holm), que ya descuenta haber probado todas.
- COSTOS: el payout se resta de forma conservadora y los empates cuentan 0 (reembolso), no como pérdida.
- DATOS SINTÉTICOS: se marcan siempre; en ruido aleatorio ninguna estrategia puede tener ventaja.
"""
import math
import numpy as np
import pandas as pd

import filtros as flt
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


# ═══════════════ Estadística ═══════════════
def p_value(z):
    """p-valor unilateral (H1: win rate > equilibrio) con aproximación normal."""
    return 0.5 * math.erfc(z / math.sqrt(2))


def holm(pvals):
    """Ajuste de Holm-Bonferroni (controla la probabilidad de algún falso positivo entre TODAS las pruebas)."""
    p = np.asarray(pvals, dtype=float)
    m = len(p)
    orden = np.argsort(p)
    ajust = np.empty(m)
    previo = 0.0
    for rango, idx in enumerate(orden):
        previo = max(previo, min(1.0, (m - rango) * p[idx]))
        ajust[idx] = previo
    return ajust


def umbral_significativo(n, payout, alpha=0.05):
    """Win rate mínimo que haría significativa (p<alpha) una muestra de n operaciones decididas."""
    be = breakeven(payout)
    zc = 1.645 if alpha == 0.05 else 2.326
    return be + zc * math.sqrt(be * (1 - be) / max(n, 1))


def _num(x):
    """numpy -> tipos nativos y NaN/inf -> None (JSON válido)."""
    if isinstance(x, dict):
        return {k: _num(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_num(v) for v in x]
    if isinstance(x, np.ndarray):
        return [_num(v) for v in x.tolist()]
    if isinstance(x, (np.bool_, bool)):
        return bool(x)
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.floating, float)):
        f = float(x)
        return f if math.isfinite(f) else None
    return x


# ═══════════════ Resultados vectorizados ═══════════════
def resultados(df, senal, expiry_candles=1, spread=0.0):
    """Devuelve (res, operada). res: +1 gana, -1 pierde, 0 empate/sin operación. Entrada = cierre de la vela
    de la señal, salida = cierre `expiry_candles` velas después. Empate = reembolso (0), como en el bróker."""
    close = df["close"].to_numpy(dtype=float)
    n = len(close)
    fut = np.full(n, np.nan)
    if n > expiry_candles:
        fut[:n - expiry_candles] = close[expiry_candles:]
    s = np.asarray(senal, dtype=int)
    diff = fut - close
    con_dato = np.isfinite(fut)
    operada = (s != 0) & con_dato
    gana = ((s == 1) & (diff > spread)) | ((s == -1) & (diff < -spread))
    empate = (np.abs(diff) <= spread) & operada
    res = np.zeros(n, dtype=np.int8)
    res[operada & gana] = 1
    res[operada & ~gana & ~empate] = -1
    return res, operada


def estadisticas(res, operada, payout, max_puntos=400):
    """Métricas completas de un tramo."""
    r = res[operada]
    n = int(len(r))
    if n == 0:
        return {**_empty_result(), "ties": 0, "p": 1.0}
    wins = int((r == 1).sum())
    losses = int((r == -1).sum())
    ties = n - wins - losses
    decididas = wins + losses
    wr = wins / decididas if decididas else 0.0
    be = breakeven(payout)
    z = (wr - be) / math.sqrt(be * (1 - be) / decididas) if decididas > 1 else 0.0
    pnl = wins * payout - losses
    pf = (wins * payout / losses) if losses > 0 else (wins * payout if wins else 0.0)
    pasos = np.where(r == 1, payout, np.where(r == -1, -1.0, 0.0))
    equity = np.cumsum(pasos)
    racha = peor = 0
    for x in r:
        if x == -1:
            racha += 1
            peor = max(peor, racha)
        elif x == 1:
            racha = 0
    if len(equity) > max_puntos:                       # aligera el JSON
        idx = np.linspace(0, len(equity) - 1, max_puntos).astype(int)
        equity = equity[idx]
    return dict(n=n, wins=wins, losses=losses, ties=ties, winrate=wr, pnl=pnl, z=z, p=p_value(z),
                ev=pnl / n, profit_factor=pf, max_loss_streak=peor, equity_curve=equity.tolist())


def _z_rapido(res, operada, payout):
    r = res[operada]
    w, l = int((r == 1).sum()), int((r == -1).sum())
    d = w + l
    if d < 2:
        return len(r), 0.0, 0.0
    wr = w / d
    be = breakeven(payout)
    return len(r), wr, (wr - be) / math.sqrt(be * (1 - be) / d)


# ═══════════════ Señales ═══════════════
def calcular_senales(df, nombres=None):
    """Señales de todas las estrategias, UNA sola vez. Los indicadores son causales (solo usan el pasado)."""
    out = {}
    for name in (nombres or list(STRATEGIES)):
        try:
            fn = STRATEGIES[name]
            sig = fn(df) if callable(fn) else fn
            if sig is None or len(sig) != len(df):
                continue
            out[name] = np.nan_to_num(np.asarray(sig, dtype=float)).astype(int)
        except Exception:
            continue
    return out


def _ventanas(n, train, test):
    v, i = [], 0
    while i + train + test <= n:
        v.append((i, i + train, i + train + test))
        i += test
    return v


def ventanas_por_defecto(n):
    train = max(400, int(n * 0.30))
    test = max(150, int(n * 0.10))
    return train, test


# ═══════════════ Walk-forward ═══════════════
def walk_forward(df, senales, expiry_candles=1, payout=0.85, train=None, test=None, min_trades=30,
                 cfg_filtros=None, pre=None, spread=0.0, exigir_train_positivo=True):
    """Elige la mejor estrategia en cada ventana de entrenamiento y la opera en la ventana siguiente.
    cfg_filtros=None no aplica filtros. Devuelve las operaciones fuera de muestra agregadas y el detalle por ventana."""
    n = len(df)
    if train is None or test is None:
        train, test = ventanas_por_defecto(n)
    vent = _ventanas(n, train, test)
    if not vent or not senales:
        return {"oos": {**_empty_result(), "ties": 0, "p": 1.0}, "folds": [], "ventanas_total": 0,
                "ventanas_operadas": 0, "ventanas_positivas": 0, "train": train, "test": test,
                "frecuencia_elegidas": {}}

    if cfg_filtros is not None and pre is None:
        pre = flt.precalcular(df, cfg_filtros)

    res_por = {}
    for name, s in senales.items():
        s2 = s if cfg_filtros is None else np.where(flt.mascara_permitida(pre, s, cfg_filtros), s, 0)
        res_por[name] = resultados(df, s2, expiry_candles, spread)

    be = breakeven(payout)
    folds, oos_res, oos_op, elegidas = [], [], [], {}
    for k, (i, j, m) in enumerate(vent):
        mejor = None
        for name, (res, op) in res_por.items():
            nt, wr, z = _z_rapido(res[i:j], op[i:j], payout)
            if nt >= min_trades and (mejor is None or z > mejor[3]):
                mejor = (name, nt, wr, z)
        fila = {"ventana": k + 1, "estrategia": None, "train_n": 0, "train_wr": None,
                "test_n": 0, "test_wr": None, "test_ev": None, "opero": False}
        if mejor is not None:
            name, nt, wr, z = mejor
            fila.update(estrategia=name, train_n=nt, train_wr=wr)
            if not exigir_train_positivo or wr > be:
                res, op = res_por[name]
                st = estadisticas(res[j:m], op[j:m], payout)
                fila.update(opero=True, test_n=st["n"], test_wr=st["winrate"], test_ev=st["ev"])
                oos_res.append(res[j:m])
                oos_op.append(op[j:m])
                elegidas[name] = elegidas.get(name, 0) + 1
        folds.append(fila)

    if oos_res:
        oos = estadisticas(np.concatenate(oos_res), np.concatenate(oos_op), payout)
    else:
        oos = {**_empty_result(), "ties": 0, "p": 1.0}
    operadas = [f for f in folds if f["opero"] and f["test_n"] > 0]
    return {"oos": oos, "folds": folds, "ventanas_total": len(vent), "ventanas_operadas": len(operadas),
            "ventanas_positivas": sum(1 for f in operadas if (f["test_ev"] or 0) > 0),
            "train": train, "test": test, "frecuencia_elegidas": elegidas}


# ═══════════════ Ranking descriptivo con Holm ═══════════════
def ranking(df, senales, expiry_candles=1, payout=0.85, test=None, min_trades=30,
            cfg_filtros=None, pre=None, spread=0.0, top=10):
    """Ranking sobre TODO el histórico (descriptivo, NO es validación). Incluye p-valor ajustado por Holm y en
    cuántas ventanas consecutivas la estrategia fue rentable."""
    n = len(df)
    _, test_def = ventanas_por_defecto(n)
    test = test or test_def
    if cfg_filtros is not None and pre is None:
        pre = flt.precalcular(df, cfg_filtros)
    filas = []
    for name, s in senales.items():
        s2 = s if cfg_filtros is None else np.where(flt.mascara_permitida(pre, s, cfg_filtros), s, 0)
        res, op = resultados(df, s2, expiry_candles, spread)
        st = estadisticas(res, op, payout, max_puntos=2)
        if st["n"] < min_trades:
            continue
        trozos = [(a, min(a + test, n)) for a in range(0, n - test + 1, test)]
        pos = tot = 0
        for a, b in trozos:
            if op[a:b].sum() >= 5:
                tot += 1
                pos += int(estadisticas(res[a:b], op[a:b], payout, max_puntos=2)["ev"] > 0)
        filas.append(dict(strategy=name, n=st["n"], winrate=st["winrate"], z=st["z"], p=st["p"],
                          ev=st["ev"], tramos_positivos=pos, tramos=tot))
    if not filas:
        return pd.DataFrame(), 0
    t = pd.DataFrame(filas)
    t["p_ajustado"] = holm(t["p"].to_numpy())
    t = t.sort_values(["p_ajustado", "z"], ascending=[True, False]).reset_index(drop=True)
    return t.head(top), len(t)


def estrategia_actual(df, senales, expiry_candles=1, payout=0.85, ventana=None, min_trades=30,
                      cfg_filtros=None, pre=None, spread=0.0):
    """La que se desplegaría hoy: mejor z en las velas MÁS RECIENTES. Su rendimiento futuro no está validado."""
    n = len(df)
    ventana = ventana or ventanas_por_defecto(n)[0]
    a = max(0, n - ventana)
    if cfg_filtros is not None and pre is None:
        pre = flt.precalcular(df, cfg_filtros)
    mejor = None
    for name, s in senales.items():
        s2 = s if cfg_filtros is None else np.where(flt.mascara_permitida(pre, s, cfg_filtros), s, 0)
        res, op = resultados(df, s2, expiry_candles, spread)
        nt, wr, z = _z_rapido(res[a:], op[a:], payout)
        if nt >= min_trades and (mejor is None or z > mejor[2]):
            mejor = (name, wr, z, nt)
    return mejor


# ═══════════════ Veredicto ═══════════════
def verdict(res, payout=0.85, min_trades=100, z_min=1.64, alpha=0.05, p_adj=None, folds=None, sintetico=False):
    """Juicio honesto sobre un resultado FUERA DE MUESTRA. Devuelve (texto, nivel) con nivel good/warn/bad.
    folds=(ventanas_positivas, ventanas_operadas). Debe aplicarse SOLO a datos no usados para elegir."""
    be = breakeven(payout)
    n = res.get("n", 0)
    if sintetico:
        return ("Datos sintéticos (ruido aleatorio): ninguna estrategia puede tener ventaja real aquí y un resultado "
                "«bueno» sería pura suerte. Sirve para probar la interfaz, no para evaluar estrategias.", "warn")
    if n < min_trades:
        return (f"Muestra muy pequeña ({n} operaciones fuera de muestra; se piden al menos {min_trades}): "
                f"el resultado no es concluyente. Descarga más velas o relaja los filtros.", "warn")
    if res["winrate"] <= be or res["ev"] <= 0:
        return (f"Sin ventaja: el win rate fuera de muestra ({res['winrate']:.1%}) no supera el punto de "
                f"equilibrio ({be:.1%}). El procedimiento «elegir la mejor y operarla» perdería dinero.", "bad")
    p = p_adj if p_adj is not None else res.get("p", 1.0)
    if p >= alpha:
        need = umbral_significativo(n, payout, alpha)
        return (f"Ventaja NO demostrada: supera el equilibrio ({be:.1%}) pero p={p:.2f} (≥{alpha}); puede ser azar. "
                f"Con {n} operaciones haría falta un win rate de {need:.1%} para ser significativo.", "warn")
    if folds and folds[1] >= 3 and folds[0] / folds[1] < 0.6:
        return (f"Inestable: rentable solo en {folds[0]} de {folds[1]} ventanas, aunque el total salga positivo "
                f"(p={p:.3f}). Una ventaja real debería repetirse.", "warn")
    return (f"Ventaja estadísticamente apoyada fuera de muestra (p={p:.3f}, win rate {res['winrate']:.1%} vs equilibrio "
            f"{be:.1%}). Confírmala en cuenta demo con 100-200 operaciones antes de arriesgar dinero real.", "good")


# ═══════════════ Análisis completo (lo que usan la API y el bot) ═══════════════
def analisis_completo(df, expiry_candles=1, payout=0.85, cfg_filtros=None, train=None, test=None,
                      min_trades=30, spread=0.0, comparar=True, sintetico=False):
    """cfg_filtros: filtros del análisis principal (None = sin filtros). Con comparar=True se corre además sin filtros
    para ver si ayudan. El veredicto usa SOLO el análisis principal (así no se elige la variante a posteriori)."""
    senales = calcular_senales(df)
    if train is None or test is None:
        train, test = ventanas_por_defecto(len(df))
    pre = flt.precalcular(df, cfg_filtros) if cfg_filtros is not None else None

    wf = walk_forward(df, senales, expiry_candles, payout, train, test, min_trades, cfg_filtros, pre, spread)
    wf_sin = (walk_forward(df, senales, expiry_candles, payout, train, test, min_trades, None, None, spread)
              if comparar and cfg_filtros is not None else None)
    top, n_cand = ranking(df, senales, expiry_candles, payout, test, min_trades, cfg_filtros, pre, spread)
    actual = estrategia_actual(df, senales, expiry_candles, payout, train, min_trades, cfg_filtros, pre, spread)

    texto, nivel = verdict(wf["oos"], payout, folds=(wf["ventanas_positivas"], wf["ventanas_operadas"]),
                           sintetico=sintetico)
    return dict(senales=len(senales), walk=wf, walk_sin_filtros=wf_sin, top=top, candidatas=n_cand,
                actual=actual, veredicto=(texto, nivel), payout=payout, train=train, test=test)


def search(df, expiry_candles=1, payout=0.85, min_trades=30, min_test=15, z_min=1.2, cfg_filtros=None):
    """Compatibilidad con bot.py y la GUI antigua: (estrategia, tabla_top, info). info['test'] es el resultado
    fuera de muestra del walk-forward (no el de la estrategia elegida)."""
    if df is None or len(df) < 600:
        return "rsi14_30_70", pd.DataFrame(), {"candidates": 0, "tested": 0, "test": {**_empty_result(), "ties": 0, "p": 1.0}}
    a = analisis_completo(df, expiry_candles, payout, cfg_filtros, min_trades=min_trades, comparar=False)
    pick = a["actual"][0] if a["actual"] else "rsi14_30_70"
    top = a["top"]
    info = {"candidates": a["candidatas"], "tested": a["senales"], "pick": pick, "test": a["walk"]["oos"],
            "veredicto": a["veredicto"], "walk": a["walk"]}
    return pick, top, info


def check_strategy(df, name, expiry_candles=1, payout=0.85, min_test=30, z_min=1.64):
    n = len(df)
    b = int(n * 0.75)
    te = evaluate(df.iloc[b:], STRATEGIES[name](df).iloc[b:], expiry_candles, payout)
    return te, (te["n"] >= min_test and te["ev"] > 0 and te["z"] > z_min)
