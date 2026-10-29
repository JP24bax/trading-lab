"""
Estrategias y Rejilla de Búsqueda Expandida con Decenas de Combinaciones Algorítmicas.
"""
import numpy as np
import pandas as pd


# ---------- INDICADORES TÉCNICOS BASE ----------

def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def sma(s, n):
    return s.rolling(n).mean()


def rsi(s, n=14):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def atr(df, n=14):
    tr = np.maximum(
        df["high"] - df["low"],
        np.maximum(abs(df["high"] - df["close"].shift()), abs(df["low"] - df["close"].shift()))
    )
    return pd.Series(tr, index=df.index).rolling(n).mean()


def stochastic(df, k_period=14, d_period=3):
    low_min = df["low"].rolling(k_period).min()
    high_max = df["high"].rolling(k_period).max()
    k = 100 * ((df["close"] - low_min) / (high_max - low_min).replace(0, np.nan))
    d = k.rolling(d_period).mean()
    return k, d


def cci(df, n=20):
    tp = (df["high"] + df["low"] + df["close"]) / 3
    ma = tp.rolling(n).mean()
    md = tp.rolling(n).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
    return (tp - ma) / (0.015 * md.replace(0, np.nan))


def williams_r(df, n=14):
    high_max = df["high"].rolling(n).max()
    low_min = df["low"].rolling(n).min()
    return -100 * ((high_max - df["close"]) / (high_max - low_min).replace(0, np.nan))


def adx(df, n=14):
    up_move = df["high"] - df["high"].shift(1)
    down_move = df["low"].shift(1) - df["low"]
    
    pos_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    neg_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    
    tr = atr(df, n)
    pos_di = 100 * (pd.Series(pos_dm, index=df.index).ewm(alpha=1/n).mean() / tr)
    neg_di = 100 * (pd.Series(neg_dm, index=df.index).ewm(alpha=1/n).mean() / tr)
    
    dx = 100 * (abs(pos_di - neg_di) / (pos_di + neg_di).replace(0, np.nan))
    return dx.ewm(alpha=1/n).mean(), pos_di, neg_di


def macd(s, fast=12, slow=26, signal=9):
    fast_ema = ema(s, fast)
    slow_ema = ema(s, slow)
    macd_line = fast_ema - slow_ema
    signal_line = ema(macd_line, signal)
    return macd_line, signal_line


def _sig(call, put):
    return pd.Series(np.where(call, 1, np.where(put, -1, 0)), index=call.index)


# ---------- DESCRIPCIONES DE ESTRATEGIAS ----------
# Cada texto describe la lógica REAL del código de build_grid().

STRATEGY_DESCRIPTIONS = {
    "adx20_trend": "ADX (14): CALL si ADX > 25 y DI+ > DI-. PUT si ADX > 25 y DI- > DI+ (sigue la tendencia fuerte).",
    "rsi_stoch_combo": "Combo RSI(14) + Estocástico(14): CALL si RSI < 30 y %K < 20. PUT si RSI > 70 y %K > 80.",
    "streak3_rev": "Reversión: CALL tras 3 velas bajistas seguidas. PUT tras 3 velas alcistas seguidas.",
    "breakout20": "Ruptura de rango (20): CALL si el cierre supera el máximo de las 20 velas previas. PUT si rompe el mínimo.",
    "engulfing_pat": "Patrón envolvente: CALL tras vela alcista que envuelve a la bajista previa. PUT en el caso contrario.",
}


def get_strategy_description(name):
    import re
    if name in STRATEGY_DESCRIPTIONS:
        return STRATEGY_DESCRIPTIONS[name]
    m = re.fullmatch(r"rsi(\d+)_(\d+)_(\d+)", name)
    if m:
        n, lo, hi = m.groups()
        return f"RSI ({n}): CALL cuando el RSI cae por debajo de {lo} (sobreventa). PUT cuando sube por encima de {hi} (sobrecompra)."
    m = re.fullmatch(r"ema(\d+)_(\d+)", name)
    if m:
        fa, sl = m.groups()
        return f"Cruce de EMAs ({fa}/{sl}): CALL cuando la EMA {fa} cruza sobre la EMA {sl}. PUT cuando cruza por debajo."
    m = re.fullmatch(r"stoch(\d+)_(\d+)_(\d+)", name)
    if m:
        k, lo, hi = m.groups()
        return f"Estocástico ({k}): CALL si %K < {lo} y está sobre %D. PUT si %K > {hi} y está bajo %D."
    m = re.fullmatch(r"cci(\d+)", name)
    if m:
        return f"CCI ({m.group(1)}): CALL al cruzar hacia abajo el nivel -100. PUT al cruzar hacia arriba el nivel +100."
    m = re.fullmatch(r"williams(\d+)", name)
    if m:
        return f"Williams %R ({m.group(1)}): CALL si %R < -80 (sobreventa). PUT si %R > -20 (sobrecompra)."
    m = re.fullmatch(r"boll(\d+)_([\d.]+)", name)
    if m:
        n, k = m.groups()
        return f"Bandas de Bollinger ({n}, {k}): CALL cuando el precio vuelve a entrar por la banda inferior. PUT cuando vuelve a entrar por la superior."
    m = re.fullmatch(r"macd(\d+)_(\d+)_(\d+)", name)
    if m:
        f_, s_, g_ = m.groups()
        return f"MACD ({f_}, {s_}, {g_}): CALL cuando la línea MACD cruza sobre la señal. PUT cuando cruza por debajo."
    m = re.fullmatch(r"keltner(\d+)_([\d.]+)", name)
    if m:
        n, k = m.groups()
        return f"Canal de Keltner ({n}, {k}×ATR): CALL en ruptura alcista del canal. PUT en ruptura bajista."
    m = re.fullmatch(r"triple_ema_(\d+)_(\d+)_(\d+)", name)
    if m:
        a_, b_, c_ = m.groups()
        return f"Triple EMA ({a_}/{b_}/{c_}): CALL cuando se alinean EMA{a_} > EMA{b_} > EMA{c_}. PUT cuando se alinean al revés."
    return "Regla personalizada o estrategia sin descripción registrada."


# ---------- MÓDULO DE REGLAS PERSONALIZADAS ----------

def custom_rule_strategy(indicator_name, condition, threshold):
    def f(df):
        c = df["close"]
        if indicator_name == "RSI": ind = rsi(c, 14)
        elif indicator_name == "EMA_Fast": ind = ema(c, 9)
        else: ind = c

        if condition == "<":
            call = ind < threshold
            put = ind > (100 - threshold if indicator_name == "RSI" else threshold * 1.01)
        elif condition == ">":
            call = ind > threshold
            put = ind < (100 - threshold if indicator_name == "RSI" else threshold * 0.99)
        else:
            call = (ind > threshold) & (ind.shift() <= threshold)
            put = (ind < threshold) & (ind.shift() >= threshold)
        return _sig(call, put)
    return f


# ---------- ESTRATEGIAS ADICIONALES ----------

def _boll(df, n, k):
    c = df["close"]
    ma, sd = c.rolling(n).mean(), c.rolling(n).std()
    up, lo = ma + k * sd, ma - k * sd
    return _sig((c.shift() < lo.shift()) & (c >= lo),
                (c.shift() > up.shift()) & (c <= up))


def _macd_cross(df, f, s_, g):
    m, sg = macd(df["close"], f, s_, g)
    return _sig((m > sg) & (m.shift() <= sg.shift()),
                (m < sg) & (m.shift() >= sg.shift()))


def _keltner(df, n, k):
    c = df["close"]
    mid, a = ema(c, n), atr(df, n)
    up, lo = mid + k * a, mid - k * a
    return _sig((c > up) & (c.shift() <= up.shift()),
                (c < lo) & (c.shift() >= lo.shift()))


def _triple_ema(df, a, b, c_):
    c = df["close"]
    e1, e2, e3 = ema(c, a), ema(c, b), ema(c, c_)
    up = (e1 > e2) & (e2 > e3)
    dn = (e1 < e2) & (e2 < e3)
    # solo en la vela en que se alinean (evita una señal en cada vela de la tendencia)
    return _sig(up & ~up.shift(fill_value=False), dn & ~dn.shift(fill_value=False))


# ---------- GENERADOR MASIVO DE CUADRÍCULA (GRID) ----------

def build_grid():
    g = {}

    # 1. Variaciones de RSI (Combinaciones de Período y Umbrales)
    for n in (5, 7, 9, 12, 14, 21):
        for lo in (15, 20, 25, 30):
            hi = 100 - lo
            g[f"rsi{n}_{lo}_{hi}"] = lambda df, n=n, lo=lo, hi=hi: _sig(
                (rsi(df["close"], n) < lo) & (rsi(df["close"], n).shift() >= lo),
                (rsi(df["close"], n) > hi) & (rsi(df["close"], n).shift() <= hi)
            )

    # 2. Cruces de Medias Móviles Exponenciales (EMA)
    fast_periods = (3, 5, 8, 9, 12, 15)
    slow_periods = (13, 21, 30, 50, 100)
    for fa in fast_periods:
        for sl in slow_periods:
            if fa < sl:
                g[f"ema{fa}_{sl}"] = lambda df, fa=fa, sl=sl: _sig(
                    (ema(df["close"], fa) > ema(df["close"], sl)) & (ema(df["close"], fa).shift() <= ema(df["close"], sl).shift()),
                    (ema(df["close"], fa) < ema(df["close"], sl)) & (ema(df["close"], fa).shift() >= ema(df["close"], sl).shift())
                )

    # 3. Oscilador Estocástico (Variaciones de %K y Niveles)
    for k_p in (8, 14, 21):
        for lo in (15, 20, 25):
            hi = 100 - lo
            g[f"stoch{k_p}_{lo}_{hi}"] = lambda df, k_p=k_p, lo=lo, hi=hi: _sig(
                (stochastic(df, k_p)[0] < lo) & (stochastic(df, k_p)[0] > stochastic(df, k_p)[1]),
                (stochastic(df, k_p)[0] > hi) & (stochastic(df, k_p)[0] < stochastic(df, k_p)[1])
            )

    # 4. Commodity Channel Index (CCI)
    for c_p in (14, 20, 30):
        g[f"cci{c_p}"] = lambda df, c_p=c_p: _sig(
            (cci(df, c_p) < -100) & (cci(df, c_p).shift() >= -100),
            (cci(df, c_p) > 100) & (cci(df, c_p).shift() <= 100)
        )

    # 5. Williams %R
    for w_p in (10, 14, 21):
        g[f"williams{w_p}"] = lambda df, w_p=w_p: _sig(
            williams_r(df, w_p) < -80,
            williams_r(df, w_p) > -20
        )

    # 6. Fuerza de Tendencia con ADX
    g["adx20_trend"] = lambda df: _sig(
        (adx(df)[0] > 25) & (adx(df)[1] > adx(df)[2]),
        (adx(df)[0] > 25) & (adx(df)[2] > adx(df)[1])
    )

    # 7. Estrategias de Combinaciones y Patrones de Velas
    g["rsi_stoch_combo"] = lambda df: _sig(
        (rsi(df["close"], 14) < 30) & (stochastic(df, 14)[0] < 20),
        (rsi(df["close"], 14) > 70) & (stochastic(df, 14)[0] > 80)
    )

    g["streak3_rev"] = lambda df: _sig(
        (df["close"] < df["open"]).rolling(3).sum() == 3,
        (df["close"] > df["open"]).rolling(3).sum() == 3
    )

    g["breakout20"] = lambda df: _sig(
        df["close"] > df["high"].rolling(20).max().shift(),
        df["close"] < df["low"].rolling(20).min().shift()
    )

    g["engulfing_pat"] = lambda df: _sig(
        (df["close"] > df["open"]) & (df["open"] < df["close"].shift()) & (df["close"] > df["open"].shift()) & (df["close"].shift() < df["open"].shift()),
        (df["close"] < df["open"]) & (df["open"] > df["close"].shift()) & (df["close"] < df["open"].shift()) & (df["close"].shift() > df["open"].shift())
    )

    # 8. Bollinger, MACD, Keltner y Triple EMA
    for n in (10, 20, 30):
        for kk in (1.5, 2.0, 2.5):
            g[f"boll{n}_{kk}"] = lambda df, n=n, kk=kk: _boll(df, n, kk)

    for f_, s_, g_ in ((8, 17, 9), (12, 26, 9), (5, 35, 5)):
        g[f"macd{f_}_{s_}_{g_}"] = lambda df, f_=f_, s_=s_, g_=g_: _macd_cross(df, f_, s_, g_)

    for n in (10, 20):
        for kk in (1.5, 2.0):
            g[f"keltner{n}_{kk}"] = lambda df, n=n, kk=kk: _keltner(df, n, kk)

    for a_, b_, c_ in ((5, 13, 34), (3, 8, 21), (8, 21, 55)):
        g[f"triple_ema_{a_}_{b_}_{c_}"] = lambda df, a_=a_, b_=b_, c_=c_: _triple_ema(df, a_, b_, c_)

    return g


STRATEGIES = build_grid()