"""Brokers. SimBroker (datos sintéticos) e IQBroker
(soporta reconexión automática con exponential backoff y manejo de mantenimiento)."""
import time
import numpy as np
import pandas as pd


class SimBroker:
    name = "sim"

    def __init__(self, seed=0, payout=0.85, balance=1000.0):
        self.rng = np.random.default_rng(seed)
        self.payout, self.balance = payout, balance
        self.price = 1.10

    def connect(self, on_status=None):
        if on_status:
            on_status("CONNECTED", "Simulación activa")
        return True

    def currency(self):
        return "USD"

    def get_payout(self, asset):
        return self.payout

    def candles(self, asset, size=60, count=200):
        r = self.rng.normal(0, 0.0004, count + 1)
        c = self.price * np.exp(np.cumsum(r))
        o = np.r_[self.price, c[:-1]]
        self.price = c[-1]
        h = np.maximum(o, c) + abs(self.rng.normal(0, 0.0001, count + 1))
        l = np.minimum(o, c) - abs(self.rng.normal(0, 0.0001, count + 1))
        return pd.DataFrame(dict(open=o, high=h, low=l, close=c))[:count]

    def candles_history(self, asset, size=60, total=15000):
        return self.candles(asset, size, total)

    def buy(self, asset, amount, direction, minutes):
        win = self.rng.random() < 0.5
        self.balance += amount * self.payout if win else -amount
        return ("sim", win, amount * self.payout if win else -amount)

    def result(self, order):
        return order[1], order[2]

    def get_balance(self):
        return self.balance


class IQBroker:
    name = "iqoption"

    def __init__(self, email, password, account="PRACTICE"):
        try:
            from iqoptionapi.stable_api import IQ_Option
        except ImportError:
            raise RuntimeError("Instala la librería: pip install iqoptionapi")
        self.email = email
        self.password = password
        self.api = IQ_Option(email, password)
        self.account = account
        self.on_status = lambda state, msg: None

    def connect_with_retry(self, max_retries=5, on_status=None):
        if on_status:
            self.on_status = on_status

        self.on_status("RECONNECTING", "Intentando conectar con IQ Option...")
        delay = 2
        for attempt in range(1, max_retries + 1):
            try:
                ok, reason = self.api.connect()
                if ok:
                    self.api.change_balance(self.account)
                    self.on_status("CONNECTED", "Conectado a IQ Option (PRACTICE)")
                    return True
                
                if "maintenance" in str(reason).lower():
                    self.on_status("DISCONNECTED", "Bróker en Mantenimiento técnico.")
                    raise RuntimeError("El bróker está en mantenimiento programado.")

            except Exception as e:
                reason = str(e)

            self.on_status("RECONNECTING", f"Reintento {attempt}/{max_retries} en {delay}s...")
            time.sleep(delay)
            delay = min(delay * 2, 60)

        self.on_status("DISCONNECTED", "Fallo permanente de conexión.")
        raise RuntimeError(f"No fue posible conectar tras {max_retries} intentos: {reason}")

    def connect(self, on_status=None):
        return self.connect_with_retry(max_retries=5, on_status=on_status)

    def _num(self, v):
        if isinstance(v, dict):
            for k in ("turbo", "binary", "profit", "payout"):
                if k in v:
                    r = self._num(v[k])
                    if r: return r
            for x in v.values():
                r = self._num(x)
                if r: return r
            return None
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        f = f / 100 if f > 1 else f
        return f if 0.3 < f < 1.0 else None

    def get_payout(self, asset):
        try:
            p = self._num(self.api.get_all_profit().get(asset))
            if p: return p
        except Exception:
            pass
        return 0.80

    def currency(self):
        try:
            cur = self.api.get_currency()
            return str(cur).upper() if cur else "USD"
        except Exception:
            return "USD"

    def candles(self, asset, size=60, count=200):
        raw = self.api.get_candles(asset, size, count + 1, time.time())
        if not raw:
            raise RuntimeError(f"Sin datos de velas para {asset}")
        df = pd.DataFrame(raw).rename(columns={"max": "high", "min": "low"})
        df = df[df["from"] + size <= time.time()].tail(count).reset_index(drop=True)
        df["t"] = df["from"]
        return df[["open", "high", "low", "close", "t"]]

    def candles_history(self, asset, size=60, total=15000):
        out, end = [], time.time()
        while len(out) < total:
            raw = self.api.get_candles(asset, size, min(1000, total - len(out)), end)
            if not raw:
                break
            out = list(raw) + out
            end = raw[0]["from"] - 1
            time.sleep(0.2)
        if not out:
            raise RuntimeError(f"No fue posible descargar histórico para {asset}")
        df = pd.DataFrame(out).drop_duplicates("from").sort_values("from")
        df = df.rename(columns={"max": "high", "min": "low"})
        return df[["open", "high", "low", "close"]].reset_index(drop=True)

    def buy(self, asset, amount, direction, minutes):
        act = "call" if direction == 1 else "put"
        try:
            ok, oid = self.api.buy(amount, asset, act, minutes)
        except Exception as e:
            raise RuntimeError(f"Error de red/comunicación al enviar orden: {e}")

        if not ok or not oid:
            raise RuntimeError(f"Orden rechazada por el bróker. Motivo/ID: {oid}")

        return oid

    def result(self, order):
        for _ in range(10):
            try:
                win, profit = self.api.check_win_v4(order)
                if win is not None:
                    return (profit > 0), profit
            except Exception:
                pass
            time.sleep(1)
        raise RuntimeError(f"Estado de orden {order} incierto. Confirmación no recibida del bróker.")

    def get_balance(self):
        return self.api.get_balance()