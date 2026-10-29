"""Utilidades de moneda COP (reconstruido para la web; API compatible con bot.py).

- cop_per_unit(cur, rate): cuántas unidades de la divisa del bróker valen 1 COP
  (es decir, amount_cop / k = unidades de cuenta).
- fmt(v, con_signo): formatea en COP con separador de miles.
- parse_num(texto): entero tolerante a separadores ("1.500.000", "1500", "1500.5").
- usd_cop(): tasa USD/COP actual (con caché y valores de respaldo).
"""

from __future__ import annotations

import json
import os
import time
import urllib.request

_RUTA_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cop_cache.json")


def cop_per_unit(cur: str, rate: float | None = None) -> float:
    """Factor k tal que importe_en_unidades = importe_COP / k.

    Con cur="USD" y tasa 4000 COP/USD: k = 4000 → 5.000 COP = 1,25 USD.
    """
    if rate and rate > 0:
        return float(rate)
    try:
        return float(usd_cop())
    except Exception:
        return 4000.0  # respaldo conservador


def fmt(v: float, con_signo: bool = False) -> str:
    """Formato COP: +$4.250 / -$3.100 (sin decimales)."""
    firma = "+" if (con_signo and v >= 0) else ""
    if v < 0:
        firma = "-"
    return f"{firma}${abs(round(v)):,.0f}".replace(",", ".")


def parse_num(texto: str | float | int) -> float:
    """Acepta "1.500.000", "1500", "1500.5" → float."""
    if isinstance(texto, (int, float)):
        return float(texto)
    limpio = str(texto).strip().replace(".", "").replace(",", ".")
    if limpio.startswith("-"):
        return -float(limpio[1:] or 0)
    return float(limpio or 0)


def usd_cop() -> float:
    """Tasa USD/COP actual. 1) cache local <1 h, 2) API pública, 3) último valor guardado."""
    ahora = time.time()
    cache = _leer_cache()
    if cache and ahora - cache.get("t", 0) < 3600:
        return cache["rate"]

    tasa = None
    try:
        req = urllib.request.Request(
            "https://open.er-api.com/v6/latest/USD",
            headers={"User-Agent": "tradinglab/1.0"},
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            datos = json.loads(r.read().decode())
            tasa = float(datos["rates"]["COP"])
    except Exception:
        tasa = None

    if not tasa:
        tasa = cache["rate"] if cache else 4000.0

    _guardar_cache(tasa)
    return tasa


def _leer_cache() -> dict | None:
    try:
        with open(_RUTA_CACHE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) and "rate" in d else None
    except Exception:
        return None


def _guardar_cache(rate: float) -> None:
    try:
        with open(_RUTA_CACHE, "w", encoding="utf-8") as f:
            json.dump({"t": time.time(), "rate": rate}, f)
    except Exception:
        pass


if __name__ == "__main__":
    print("fmt:", fmt(4250, True), fmt(-3100), fmt(1234567))
    print("parse:", parse_num("1.500.000"), parse_num(1500), parse_num("1500.5"))
    print("tasa:", usd_cop())
