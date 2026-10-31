"""Análisis de las operaciones reales/demo del bot: ¿en qué condiciones se gana o se pierde? y
¿hay evidencia suficiente para pasar de demo a dinero real?

Reglas de honestidad:
- Un grupo con menos de MIN_GRUPO operaciones se muestra pero se marca como ruido (no se saca ninguna conclusión).
- Las operaciones simuladas (azar) o de origen desconocido NO cuentan para la puerta de demo.
- Si se mira un grupo entre muchos, el p-valor se ajusta por el número de grupos revisados.
"""
from __future__ import annotations

import math
from collections import defaultdict

import backtest as bt

MIN_GRUPO = 100            # operaciones mínimas por grupo para sacar conclusiones
MIN_DEMO = 100             # mínimo para empezar a opinar
OBJETIVO_DEMO = 200        # mínimo para declarar ventaja respaldada
PAYOUT_DEFECTO = 0.85


def _wilson(wins, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = wins / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def _payout(t):
    p = t.get("payout")
    return p if isinstance(p, (int, float)) and 0.3 < p < 1.0 else PAYOUT_DEFECTO


def _resumen(trades):
    n = len(trades)
    wins = sum(1 for t in trades if t.get("won"))
    pay = sum(_payout(t) for t in trades) / n if n else PAYOUT_DEFECTO
    wr = wins / n if n else 0.0
    be = bt.breakeven(pay)
    z = (wr - be) / math.sqrt(be * (1 - be) / n) if n > 1 else 0.0
    return n, wins, wr, pay, be, z


# ── Etiquetas de contexto ───────────────────────────────────────────────────
def _bucket_atr(t):
    r = (t.get("contexto") or {}).get("atr_ratio")
    if r is None:
        return "sin dato"
    return "plano (<0.8)" if r < 0.8 else "normal (0.8-1.4)" if r <= 1.4 else "alto (>1.4)"


def _bucket_tendencia(t):
    ctx = t.get("contexto") or {}
    if "tendencia_tf" not in ctx:
        return "sin dato"
    tend, d = ctx["tendencia_tf"], t.get("direction", 0)
    return "sin tendencia clara" if tend == 0 else "a favor de 15m" if tend == d else "en contra de 15m"


def _hora(t):
    h = (t.get("contexto") or {}).get("hora_col")
    return "sin dato" if h is None else f"{int(h):02d}h"


DIMENSIONES = {
    "hora": _hora,
    "activo": lambda t: t.get("asset", "?"),
    "estrategia": lambda t: t.get("strategy", "?"),
    "direccion": lambda t: "CALL" if t.get("direction") == 1 else "PUT",
    "volatilidad": _bucket_atr,
    "tendencia": _bucket_tendencia,
}


def analizar_operaciones(trades, alpha=0.05):
    """Agrupa por condición y marca qué grupos tienen muestra suficiente. Solo cuenta operaciones no simuladas."""
    validas = [t for t in trades if t.get("sim") is False]
    excl = len(trades) - len(validas)
    salida = {"total": len(trades), "contadas": len(validas), "excluidas_sim": excl,
              "min_grupo": MIN_GRUPO, "dimensiones": {}}
    for dim, fn in DIMENSIONES.items():
        grupos = defaultdict(list)
        for t in validas:
            grupos[fn(t)].append(t)
        filas = []
        suficientes = [g for g in grupos.values() if len(g) >= MIN_GRUPO]
        m = max(len(suficientes), 1)
        for nombre, g in sorted(grupos.items()):
            n, wins, wr, pay, be, z = _resumen(g)
            ev = wr * pay - (1 - wr)
            ok = n >= MIN_GRUPO
            filas.append({
                "grupo": nombre, "n": n, "wins": wins, "winrate": wr, "equilibrio": be, "ev": ev,
                "p_ajustado": min(1.0, bt.p_value(z) * m) if ok else None,
                "suficiente": ok,
                "lectura": ("ruido: muestra insuficiente" if not ok else
                            "supera el equilibrio" if wr > be and bt.p_value(z) * m < alpha else
                            "no se distingue del azar" if wr > be else "por debajo del equilibrio"),
            })
        salida["dimensiones"][dim] = filas
    return salida


def veredicto_demo(trades, alpha=0.05):
    """Puerta de la cuenta demo: ¿hay evidencia para considerar dinero real? Solo cuenta demo real y una estrategia."""
    validas = [t for t in trades if t.get("sim") is False]
    excl = len(trades) - len(validas)
    base = {"contadas": len(validas), "excluidas_sim": excl, "minimo": MIN_DEMO, "objetivo": OBJETIVO_DEMO}
    if not validas:
        return {**base, "nivel": "warn", "n": 0, "estrategia": None,
                "texto": ("Aún no hay operaciones en cuenta demo real"
                          + (f" ({excl} simuladas o de origen desconocido no cuentan, son azar)." if excl else ".")
                          + f" Hacen falta al menos {MIN_DEMO}, idealmente {OBJETIVO_DEMO}, con UNA estrategia fija.")}

    por_estrategia = defaultdict(list)
    for t in validas:
        por_estrategia[t.get("strategy", "?")].append(t)
    estrategia, grupo = max(por_estrategia.items(), key=lambda kv: len(kv[1]))
    k = len(por_estrategia)
    n, wins, wr, pay, be, z = _resumen(grupo)
    lo, hi = _wilson(wins, n)
    p = min(1.0, bt.p_value(z) * k)
    d = {**base, "estrategia": estrategia, "n": n, "winrate": wr, "equilibrio": be, "payout_medio": pay,
         "ic95": [lo, hi], "p": p, "estrategias_usadas": k}
    mezcla = f" Se usaron {k} estrategias distintas; se evalúa la más usada ({estrategia}) con p ajustado por {k}." if k > 1 else ""

    if n < MIN_DEMO:
        return {**d, "nivel": "warn", "faltan": MIN_DEMO - n,
                "texto": f"Muestra insuficiente: {n} operaciones con «{estrategia}» (mínimo {MIN_DEMO}, objetivo {OBJETIVO_DEMO}). "
                         f"Con tan pocas, el win rate ({wr:.1%}) es casi ruido: su intervalo al 95 % va de {lo:.0%} a {hi:.0%}.{mezcla}"}
    if wr <= be:
        return {**d, "nivel": "bad",
                "texto": f"Sin ventaja: «{estrategia}» tiene {wr:.1%} de acierto en {n} operaciones demo, por debajo del "
                         f"equilibrio ({be:.1%}). No pases a dinero real.{mezcla}"}
    if p >= alpha:
        return {**d, "nivel": "warn",
                "texto": f"Ventaja no demostrada: {wr:.1%} vs equilibrio {be:.1%} en {n} operaciones (p={p:.2f}). "
                         f"El intervalo al 95 % ({lo:.1%}-{hi:.1%}) incluye el equilibrio: puede ser azar. Sigue en demo.{mezcla}"}
    if n < OBJETIVO_DEMO:
        return {**d, "nivel": "warn", "faltan": OBJETIVO_DEMO - n,
                "texto": f"Prometedor pero provisional: {wr:.1%} vs equilibrio {be:.1%} (p={p:.3f}) con {n} operaciones. "
                         f"Completa al menos {OBJETIVO_DEMO} antes de decidir.{mezcla}"}
    return {**d, "nivel": "good",
            "texto": f"Ventaja respaldada en demo: {wr:.1%} vs equilibrio {be:.1%} en {n} operaciones (p={p:.3f}; "
                     f"IC95 {lo:.1%}-{hi:.1%}). Aun así, el dinero real añade slippage y presión emocional: empieza con el "
                     f"importe mínimo.{mezcla}"}
