"""
TradingLab Engine · Servicio del motor de trading (Fase 1 de la migración)
==========================================================================
Envuelve los módulos del bot de escritorio (bot.py, broker.py, backtest.py,
strategies.py, ai_filter.py) como API HTTP + WebSocket.

Los eventos del motor (bot.ON_EVENT) se difunden a todos los clientes
conectados por WebSocket: log, balance, trade, badge_status, conn_status,
ai_veto… exactamente los mismos que consumía la GUI de Tkinter.

Ejecutar:  uvicorn web_service:app --port 8100   (desde bot-desktop/)
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Set

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import bot
import backtest
from ai_filter import LABELS, PROVIDERS, ai_ping, get_key, save_key
from strategies import STRATEGIES, custom_rule_strategy, get_strategy_description

app = FastAPI(title="TradingLab Engine", version="1.0.0")

# La web corre en localhost:3000 (dev) y en Vercel (prod). CLIENT_URL permite
# anadir el dominio real de produccion sin tocar el codigo (var. de entorno).
_origins = ["http://localhost:3000", "http://127.0.0.1:3000", "https://trading-lab-chi.vercel.app"]
if os.getenv("CLIENT_URL"):
    _origins.append(os.getenv("CLIENT_URL").rstrip("/"))
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Difusión de eventos ─────────────────────────────────────────────────────
clientes: Set[WebSocket] = set()
loop_principal: Optional[asyncio.AbstractEventLoop] = None


def _difundir(kind: str, data: Dict[str, Any] | None = None, **extra: Any) -> None:
    """Puente entre el hilo del motor y el loop de asyncio (thread-safe).

    Acepta dict (JSON ya listo) o kwargs sueltos: el motor llama
    ON_EVENT("conn_status", state=..., msg=...) con keywords.
    """
    datos = {**(data or {}), **extra}
    if loop_principal is None:
        return
    mensaje = json.dumps({"kind": kind, **datos}, ensure_ascii=False)
    if clientes:
        asyncio.run_coroutine_threadsafe(_difundir_async(mensaje), loop_principal)


async def _difundir_async(mensaje: str) -> None:
    muertos: List[WebSocket] = []
    for ws in list(clientes):
        try:
            await ws.send_text(mensaje)
        except Exception:
            muertos.append(ws)
    for ws in muertos:
        clientes.discard(ws)


# El motor llama a bot.ON_EVENT desde su hilo: lo redirigimos al bus.
bot.ON_EVENT = _difundir


@app.on_event("startup")
async def _al_arrancar() -> None:
    global loop_principal
    loop_principal = asyncio.get_running_loop()


# ── Estado de sesión en el servidor ────────────────────────────────────────
class EstadoSesion:
    def __init__(self) -> None:
        self.hilo: Optional[threading.Thread] = None
        self.config: Optional[SimpleNamespace] = None
        self.activo = False

    def arrancar(self, config: SimpleNamespace) -> bool:
        if self.activo:
            return False
        bot.STOP.clear()
        bot.STOP_SOFT.clear()
        self.config = config

        def trabajo():
            self.activo = True
            try:
                bot.cmd_run(config)
            except Exception as e:
                _difundir("log", {"text": f"✖ No se pudo iniciar el bot: {e}"})
                _difundir("badge_status", state="STOPPED", text="Detenido")
            finally:
                self.activo = False
                _difundir("session_done", {})

        self.hilo = threading.Thread(target=trabajo, daemon=True)
        self.hilo.start()
        return True

    def parar(self, suave: bool) -> None:
        (bot.STOP_SOFT if suave else bot.STOP).set()


sesion = EstadoSesion()


# ── Modelos de petición ─────────────────────────────────────────────────────
class ConfigSesion(BaseModel):
    """Espejo web de los campos que la GUI de Tkinter enviaba a bot.cmd_run."""
    sim: bool = True
    assets: List[str] = Field(default=["EURUSD"])
    amount: float = Field(default=5000, gt=0)          # COP por operación
    expiry: int = Field(default=1, ge=1, le=15)        # minutos
    history: int = Field(default=15000, ge=200)
    strategy: str = "auto"                             # "auto" o clave de STRATEGIES
    ai: str = "none"                                   # none | groq | gemini
    max_loss: float = 50000                            # COP
    max_streak: int = 4
    max_trades: int = 30
    start_hour: int = Field(default=0, ge=0, le=24)
    end_hour: int = Field(default=24, ge=0, le=24)
    risk_streak_trigger: int = 2
    risk_reduction_pct: float = Field(default=50, ge=0, le=90)
    cop_rate: Optional[float] = None
    email: Optional[str] = None
    password: Optional[str] = None


class PeticionStop(BaseModel):
    modo: str = "now"   # "now" | "soft"


class PeticionBacktest(BaseModel):
    asset: str = "EURUSD"
    sim: bool = True
    history: int = Field(default=5000, ge=200)
    expiry: int = Field(default=1, ge=1, le=15)
    email: Optional[str] = None
    password: Optional[str] = None


class ReglaPersonalizada(BaseModel):
    nombre: str
    indicador: str   # RSI | EMA_Fast | Precio
    condicion: str   # "<" | ">" | "cruce"
    valor: float


class PeticionAIPing(BaseModel):
    proveedor: str   # groq | gemini
    clave: Optional[str] = None


# ── Helpers ─────────────────────────────────────────────────────────────────
def _broker(sim: bool, email: Optional[str], password: Optional[str]):
    """Crea y conecta un broker. Reutiliza bot.make_broker (login.txt/entorno)."""
    return bot.make_broker(sim, on_status=lambda state, msg: _difundir("conn_status", state=state, msg=msg),
                           email=email, password=password)


# ── Endpoints HTTP ──────────────────────────────────────────────────────────
@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "sesion_activa": sesion.activo,
        "estrategias": len(STRATEGIES),
        "server_time": time.time(),
    }


@app.get("/strategies")
def listar_estrategias() -> dict:
    """Catálogo para el selector de la web (nombre + descripción legible)."""
    return {
        "total": len(STRATEGIES),
        "estrategias": [
            {"nombre": n, "descripcion": get_strategy_description(n)}
            for n in sorted(STRATEGIES)
        ],
    }


@app.post("/session/start")
def iniciar_sesion(cfg: ConfigSesion) -> dict:
    """Arranca el bucle del bot con la config de la web (equivalente a do_start)."""
    if not sesion.arrancar(SimpleNamespace(**cfg.model_dump())):
        raise HTTPException(status_code=409, detail="Ya hay una sesión en marcha")
    return {"status": "STARTED"}


@app.post("/session/stop")
def parar_sesion(p: PeticionStop) -> dict:
    """Dos modos de parada: now (inmediata) y soft (al cerrar operaciones abiertas)."""
    if p.modo not in ("now", "soft"):
        raise HTTPException(status_code=400, detail="modo debe ser 'now' o 'soft'")
    if not sesion.activo:
        return {"status": "IDLE"}
    sesion.parar(suave=(p.modo == "soft"))
    return {"status": "STOPPING", "modo": p.modo}


@app.get("/session/state")
def estado_sesion() -> dict:
    """Estado persistido (trades acumulados) para recuperar tras recargar la página."""
    guardado = bot.load_session_state() or {}
    trades = guardado.get("trades", [])
    return {
        "activo": sesion.activo,
        "trades": trades,
        "metricas": bot.compute_metrics(trades),
    }


@app.post("/backtest/search")
def backtest_busqueda(p: PeticionBacktest) -> dict:
    """Corre la búsqueda masiva (80+ estrategias) sobre histórico real o sintético.
    Equivalente a do_analyze de la GUI. Puede tardar varios segundos."""
    try:
        b = _broker(p.sim, p.email, p.password)
        pay = b.get_payout(p.asset)
        df = b.candles_history(p.asset, 60, p.history)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Error de conexión con el bróker: {e}")

    name, tabla, info = backtest.search(df, expiry_candles=p.expiry, payout=pay)
    te = info.get("test", backtest._empty_result())
    texto, nivel = backtest.verdict(te, pay)
    return {
        "elegida": name,
        "descripcion": get_strategy_description(name),
        "candidates": info.get("candidates", 0),
        "tested": info.get("tested", 0),
        "payout": pay,
        "velas": len(df),
        "test": {k: te[k] for k in ("n", "wins", "winrate", "pnl", "z", "ev", "profit_factor", "max_loss_streak") if k in te},
        "equity_curve": te.get("equity_curve", []),
        "veredicto": {"texto": texto, "nivel": nivel},
        "top10": tabla.round(3).to_dict("records") if hasattr(tabla, "to_dict") else [],
    }


@app.post("/backtest/single")
def backtest_simple(nombre: str, asset: str = "EURUSD", sim: bool = True,
                    history: int = 5000, expiry: int = 1) -> dict:
    """Prueba una estrategia concreta en el tramo de prueba (25 % final)."""
    if nombre not in STRATEGIES:
        raise HTTPException(status_code=404, detail=f"Estrategia '{nombre}' no existe")
    try:
        b = _broker(sim, None, None)
        df = b.candles_history(asset, 60, history)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Error de conexión con el bróker: {e}")

    n = len(df)
    corte = int(n * 0.75)
    sig = STRATEGIES[nombre](df)
    res = backtest.evaluate(df.iloc[corte:], sig.iloc[corte:], expiry_candles=expiry)
    texto, nivel = backtest.verdict(res)
    return {
        "estrategia": nombre,
        "descripcion": get_strategy_description(nombre),
        "res": {k: res[k] for k in ("n", "wins", "winrate", "pnl", "z", "ev", "profit_factor", "max_loss_streak") if k in res},
        "equity_curve": res.get("equity_curve", []),
        "veredicto": {"texto": texto, "nivel": nivel},
    }


@app.post("/strategies/custom")
def guardar_regla(r: ReglaPersonalizada) -> dict:
    """Editor visual de reglas: registra una estrategia dinámica en el motor vivo."""
    if not r.nombre or any(c in r.nombre for c in " ,;()"):
        raise HTTPException(status_code=400, detail="Nombre inválido")
    if r.indicador not in ("RSI", "EMA_Fast", "Precio"):
        raise HTTPException(status_code=400, detail="Indicador no soportado")
    STRATEGIES[r.nombre] = custom_rule_strategy(r.indicador, r.condicion, r.valor)
    return {"status": "OK", "nombre": r.nombre,
            "descripcion": f"Regla dinámica [{r.indicador} {r.condicion} {r.valor}]"}


@app.post("/ai/ping")
def ai_probar(p: PeticionAIPing) -> dict:
    """Prueba la clave del proveedor de IA (Groq/Gemini)."""
    if p.proveedor not in PROVIDERS:
        raise HTTPException(status_code=400, detail="Elige groq o gemini")
    if p.clave:
        save_key(p.proveedor, p.clave)
    elif not get_key(p.proveedor):
        return {"ok": False, "msg": f"Falta la clave API de {LABELS[p.proveedor]}"}
    ok, msg = ai_ping(p.proveedor)
    return {"ok": ok, "msg": msg}


@app.get("/ai/labels")
def ai_etiquetas() -> dict:
    return {"labels": LABELS}


# ── WebSocket ───────────────────────────────────────────────────────────────
@app.websocket("/ws")
async def ws_socket(ws: WebSocket):
    await ws.accept()
    clientes.add(ws)
    try:
        # Estado inicial: el cliente recién conectado recibe la sesión persistida.
        guardado = bot.load_session_state() or {}
        trades = guardado.get("trades", [])
        await ws.send_text(json.dumps({
            "kind": "init", "activo": sesion.activo, "trades": trades,
            "metricas": bot.compute_metrics(trades),
        }, ensure_ascii=False))
        # Mantiene la conexión viva (pings del cliente).
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        clientes.discard(ws)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8100)
