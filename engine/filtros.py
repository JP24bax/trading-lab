"""Filtros de contexto de mercado, compartidos por el backtest (vectorizado) y el bot en vivo.

Idea: una misma función calcula, para cada vela, si una señal CALL/PUT está permitida. El backtest la aplica
a todo el histórico y el bot a la última vela, así lo que se mide es lo mismo que se opera.

Ningún filtro crea ventaja por sí solo: solo reducen el número de operaciones. Si ayudan o no se mide con
`backtest.comparar_filtros` y, en vivo, con `analisis.analizar_operaciones`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from strategies import atr, ema

TZ_COLOMBIA = -5            # UTC-5 sin horario de verano
TF_SUPERIOR_SEG = 900       # 15 minutos

FILTROS_DEFECTO = dict(
    volatilidad=True,        # evita mercado plano o con volatilidad extrema (ATR)
    atr_min=0.6,             # ATR actual / mediana del ATR reciente: por debajo = mercado plano
    atr_max=2.0,             # por encima = volatilidad extrema
    tendencia=True,          # no operar contra la tendencia de 15 minutos
    horario=None,            # (hora_inicio, hora_fin) en hora Colombia, o None
)


# ── Calendario ──────────────────────────────────────────────────────────────
def hora_local(ahora_utc: datetime | None = None, tz: int = TZ_COLOMBIA) -> datetime:
    ahora_utc = ahora_utc or datetime.now(timezone.utc)
    return ahora_utc + timedelta(hours=tz)


def mercado_abierto(asset: str, ahora_utc: datetime | None = None) -> bool:
    """Forex regular cierra de viernes ~22:00 UTC a domingo ~22:00 UTC. Los activos '-OTC' cotizan 24/7
    pero son cotizaciones generadas por el bróker, no el mercado real."""
    if str(asset).upper().endswith("-OTC"):
        return True
    ahora_utc = ahora_utc or datetime.now(timezone.utc)
    wd, h = ahora_utc.weekday(), ahora_utc.hour     # lunes=0 ... domingo=6
    if wd == 5:
        return False
    if wd == 4 and h >= 22:
        return False
    if wd == 6 and h < 22:
        return False
    return True


def en_horario(hora: int, inicio: int, fin: int) -> bool:
    """Ventana [inicio, fin) en horas 0-24; admite ventanas que cruzan la medianoche (ej. 22 a 6)."""
    if inicio == 0 and fin == 24:
        return True
    if inicio <= fin:
        return inicio <= hora < fin
    return hora >= inicio or hora < fin


# ── Volatilidad ─────────────────────────────────────────────────────────────
def atr_ratio(df: pd.DataFrame, n: int = 14, ventana: int = 100) -> pd.Series:
    """ATR actual dividido entre la mediana de los ATR de las últimas `ventana` velas (solo pasado)."""
    a = atr(df, n)
    base = a.rolling(ventana, min_periods=ventana // 2).median()
    return a / base.replace(0, np.nan)


# ── Tendencia en temporalidad superior ──────────────────────────────────────
def tendencia_serie(close: pd.Series) -> pd.Series:
    """+1 tendencia alcista (cierre > EMA20 > EMA50), -1 bajista, 0 sin tendencia clara."""
    e20, e50 = ema(close, 20), ema(close, 50)
    up = (close > e20) & (e20 > e50)
    dn = (close < e20) & (e20 < e50)
    return pd.Series(np.where(up, 1, np.where(dn, -1, 0)), index=close.index)


def tendencia_desde_1m(df: pd.DataFrame, tf_seg: int = TF_SUPERIOR_SEG) -> np.ndarray:
    """Tendencia de la temporalidad superior alineada con cada vela de 1 minuto, SIN mirar al futuro:
    cada vela usa la última vela superior ya CERRADA. Requiere la columna 't' (inicio de cada vela, en segundos).
    Si no hay 't' devuelve ceros (el filtro no bloquea nada)."""
    if "t" not in df.columns:
        return np.zeros(len(df), dtype=int)
    grupo = (df["t"].astype("int64") // tf_seg).to_numpy()
    cierre = df["close"].groupby(grupo).last()               # cierre de cada vela superior
    tend = tendencia_serie(cierre.reset_index(drop=True)).to_numpy()
    tend_por_grupo = pd.Series(tend, index=cierre.index)
    # la vela superior en curso (grupo k) solo puede usar la ya cerrada (la anterior en el índice)
    previa = tend_por_grupo.shift(1)
    return previa.reindex(grupo).fillna(0).astype(int).to_numpy()


def tendencia_actual(df_tf: pd.DataFrame) -> int:
    """Tendencia de la última vela cerrada de un DataFrame ya en la temporalidad superior (uso en vivo)."""
    if df_tf is None or len(df_tf) < 55:
        return 0
    return int(tendencia_serie(df_tf["close"]).iloc[-1])


# ── Aplicación sobre señales ────────────────────────────────────────────────
def precalcular(df: pd.DataFrame, cfg: dict | None = None) -> dict:
    """Calcula UNA vez lo que los filtros necesitan del histórico (así se prueban 92 estrategias sin recalcular)."""
    cfg = {**FILTROS_DEFECTO, **(cfg or {})}
    pre = {"ratio": atr_ratio(df).to_numpy(), "tend": tendencia_desde_1m(df), "hora_ok": None}
    if cfg["horario"] and "t" in df.columns:
        ini, fin = cfg["horario"]
        horas = ((df["t"].astype("int64").to_numpy() // 3600) + TZ_COLOMBIA) % 24
        pre["hora_ok"] = np.array([en_horario(int(h), ini, fin) for h in horas])
    return pre


def mascara_permitida(pre: dict, senal: np.ndarray, cfg: dict | None = None) -> np.ndarray:
    """Array booleano: True donde la señal CALL(1)/PUT(-1) está permitida por los filtros activos."""
    cfg = {**FILTROS_DEFECTO, **(cfg or {})}
    s = np.asarray(senal, dtype=int)
    ok = np.ones(len(s), dtype=bool)
    if cfg["volatilidad"]:
        r = pre["ratio"]
        ok &= np.isfinite(r) & (r >= cfg["atr_min"]) & (r <= cfg["atr_max"])
    if cfg["tendencia"]:
        t = pre["tend"]
        ok &= ~((s == 1) & (t == -1)) & ~((s == -1) & (t == 1))   # solo bloquea ir CONTRA la tendencia
    if pre.get("hora_ok") is not None:
        ok &= pre["hora_ok"]
    return ok


def evaluar_en_vivo(df: pd.DataFrame, direccion: int, cfg: dict | None, df_tf: pd.DataFrame | None):
    """Para la última vela de `df`. Devuelve (permitido: bool, motivo: str, contexto: dict).
    El contexto se guarda en cada operación para poder analizar después en qué condiciones se gana o se pierde."""
    cfg = {**FILTROS_DEFECTO, **(cfg or {})}
    r = atr_ratio(df)
    ratio = float(r.iloc[-1]) if len(r) and np.isfinite(r.iloc[-1]) else None
    tend = tendencia_actual(df_tf) if df_tf is not None else 0
    ctx = {"atr_ratio": None if ratio is None else round(ratio, 3), "tendencia_tf": tend}

    if cfg["volatilidad"]:
        if ratio is None:
            return False, "volatilidad sin datos suficientes", ctx
        if ratio < cfg["atr_min"]:
            return False, f"mercado plano (ATR x{ratio:.2f} < {cfg['atr_min']})", ctx
        if ratio > cfg["atr_max"]:
            return False, f"volatilidad extrema (ATR x{ratio:.2f} > {cfg['atr_max']})", ctx
    if cfg["tendencia"] and tend == -direccion:
        lado = "alcista" if tend == 1 else "bajista"
        return False, f"va contra la tendencia {lado} de 15 min", ctx
    return True, "ok", ctx
