// motor.js — Cliente del TradingLab Engine (:8100).
// WebSocket con reconexión exponencial + helpers HTTP de la API del motor.
// Los eventos llegan con el mismo vocabulario que consumía la GUI de Tkinter:
// {kind: "log"|"trade"|"balance"|"badge_status"|"conn_status"|"ai_veto"|"init"|"session_done", ...}

// URL del motor configurable: localStorage manda (pestaña Configuración);
// en dev cae a localhost:8100. En producción con https el WS usa wss://.
function urlMotor() {
  try {
    const guardada = JSON.parse(localStorage.getItem("tradinglab.motor.cfg") || "null");
    if (guardada && guardada.engine_url) return guardada.engine_url.replace(/\/$/, "");
  } catch { }
  return "http://localhost:8100";
}
function MOTOR_HTTP() { return urlMotor(); }
function MOTOR_WS() { return urlMotor().replace(/^http/, "ws") + "/ws"; }

let socket = null;
let reintentos = 0;
const oyentes = {}; // kind -> Set<fn>
let temporizador = null;

// ── Conexión con backoff exponencial (2s, 4s, 8s… máx 30s) ────────────────
export function conectarMotor() {
  if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) return;
  try {
    socket = new WebSocket(MOTOR_WS());
  } catch {
    programarReconexion();
    return;
  }

  socket.onopen = () => {
    reintentos = 0;
    emitir("conn_status", { state: "CONNECTED", msg: "Conectado al motor" });
  };

  socket.onmessage = (ev) => {
    try {
      const msg = JSON.parse(ev.data);
      emitir(msg.kind, msg);
    } catch {
      /* mensaje ilegible: ignorar */
    }
  };

  socket.onclose = () => {
    socket = null;
    emitir("conn_status", { state: "DISCONNECTED", msg: "Motor sin conexión" });
    programarReconexion();
  };

  socket.onerror = () => socket && socket.close();
}

function programarReconexion() {
  if (temporizador) return;
  const espera = Math.min(2000 * 2 ** reintentos, 30000);
  reintentos++;
  temporizador = setTimeout(() => {
    temporizador = null;
    conectarMotor();
  }, espera);
}

// Pings para mantener viva la conexión.
setInterval(() => {
  if (socket && socket.readyState === WebSocket.OPEN) socket.send("ping");
}, 20000);

// ── Bus de eventos ──────────────────────────────────────────────────────────
export function onMotor(kind, fn) {
  (oyentes[kind] ||= new Set()).add(fn);
  return () => oyentes[kind].delete(fn);
}

function emitir(kind, datos) {
  for (const fn of oyentes[kind] || []) fn(datos);
  for (const fn of oyentes["*"] || []) fn(datos);
}

// ── API HTTP del motor ──────────────────────────────────────────────────────
async function api(path, cuerpo = null) {
  const opciones = { method: cuerpo ? "POST" : "GET", headers: { "Content-Type": "application/json" } };
  if (cuerpo) opciones.body = JSON.stringify(cuerpo);
  const r = await fetch(MOTOR_HTTP() + path, opciones);
  if (!r.ok) {
    const detalle = await r.json().catch(() => ({}));
    throw new Error(detalle.detail || `HTTP ${r.status}`);
  }
  return r.json();
}

export const iniciarSesion = (cfg) => api("/session/start", cfg);
export const pararSesion = (modo) => api("/session/stop", { modo });
export const estadoSesion = () => api("/session/state");
export const listarEstrategias = () => api("/strategies");
export const backtestBusqueda = (p) => api("/backtest/search", p);
export const backtestSingle = (nombre, p = {}) => {
  const q = new URLSearchParams({
    nombre, asset: p.asset || "EURUSD", sim: String(p.sim !== false), history: String(p.history || 5000),
    expiry: String(p.expiry || 1), filtro_volatilidad: String(p.filtro_volatilidad !== false),
    filtro_tendencia: String(p.filtro_tendencia !== false),
  });
  return api(`/backtest/single?${q}`, {});
};
export const guardarRegla = (r) => api("/strategies/custom", r);
export const analisisOperaciones = () => api("/session/analisis");
export const veredictoDemo = () => api("/session/demo");
export const aiPing = (proveedor, clave) => api("/ai/ping", { proveedor, clave });

// Estado inicial (sesión persistida) por HTTP, por si el WS tarda.
export async function recuperarEstado() {
  try { return await estadoSesion(); } catch { return null; }
}
