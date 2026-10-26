// dashboard.js — Pestaña "Motor" del bot: KPIs en vivo, curva de equity,
// tabla de operaciones (filtros/orden/CSV/modal) y registro.
import { conectarMotor, onMotor, recuperarEstado, iniciarSesion, pararSesion } from "./motor.js";

const $ = (id) => document.getElementById(id);

// ─ Estado local del dashboard ───────────
const CLAVE = "tradinglab.motor.v1";
let trades = [];              // historial acumulado (persistido en el motor)
let filtros = { activo: "Todos", texto: "", orden: "idx", asc: false };
let curvaEquity = [];         // [{ t, saldo }]

function cargarLocal() {
  try {
    const g = JSON.parse(localStorage.getItem(CLAVE) || "{}");
    filtros = { ...filtros, ...(g.filtros || {}) };
  } catch { /* datos ilegibles */ }
}
function guardarLocal() {
  try { localStorage.setItem(CLAVE, JSON.stringify({ filtros })); } catch { }
}

// ─ Formato COP (igual que el backend) ───────────
function fmtCop(v, conSigno = false) {
  const s = conSigno && v >= 0 ? "+" : v < 0 ? "-" : "";
  return `${s}$${Math.abs(Math.round(v)).toLocaleString("es-CO")}`.replace(/\./g, ",");
}
function fmtPct(v) { return (v * 100).toLocaleString("es-CO", { maximumFractionDigits: 1 }) + "%"; }

// ─ Métricas (espejo de bot.compute_metrics) ─────────────
function metricas(list) {
  if (!list.length) return { total: 0, wins: 0, winrate: 0, pnl: 0, ev: 0, max_drawdown: 0, best_streak: 0, worst_streak: 0 };
  let wins = 0, pnl = 0, best = 0, worst = 0, cw = 0, cl = 0, pico = 0, dd = 0;
  for (const t of list) {
    wins += t.won ? 1 : 0;
    pnl += t.profit;
    cw = t.won ? cw + 1 : 0;
    cl = t.won ? 0 : cl + 1;
    best = Math.max(best, cw);
    worst = Math.max(worst, cl);
    pico = Math.max(pico, pnl);
    dd = Math.max(dd, pico - pnl);
  }
  return { total: list.length, wins, winrate: wins / list.length, pnl, ev: pnl / list.length, max_drawdown: dd, best_streak: best, worst_streak: worst };
}

// ─ KPIs ───────────
function pintarKPIs() {
  const m = metricas(trades);
  const set = (id, texto, clase = "") => {
    const el = $(id);
    if (!el) return;
    el.textContent = texto;
    el.className = "kpi-valor " + clase;
  };
  const saldo = trades.length ? trades[trades.length - 1].balance : null;
  set("kpi-balance", saldo == null ? "—" : fmtCop(saldo));
  set("kpi-pnl", fmtCop(m.pnl, true), m.pnl > 0 ? "positivo" : m.pnl < 0 ? "negativo" : "");
  set("kpi-trades", String(m.total));
  set("kpi-wr", m.total ? fmtPct(m.winrate) : "—", m.winrate >= 0.5405 ? "positivo" : m.total ? "warn" : "");
  set("kpi-ev", m.total ? fmtCop(m.ev, true) : "—", m.ev > 0 ? "positivo" : m.total ? "negativo" : "");
  set("kpi-dd", m.max_drawdown ? "-" + fmtCop(m.max_drawdown).slice(1) : "$0", m.max_drawdown ? "negativo" : "");
  set("kpi-best", String(m.best_streak), m.best_streak ? "positivo" : "");
  set("kpi-worst", String(m.worst_streak), m.worst_streak ? "negativo" : "");
  pintarCurva();
}

// ─ Curva de equity (SVG propio, sin librerías) ───────────
function pintarCurva() {
  const svg = $("grafica-equity");
  if (!svg) return;
  const serie = curvaEquity.length > 1 ? curvaEquity : trades.map((t, i) => ({ t: i, saldo: t.balance }));
  if (serie.length < 2) { svg.replaceChildren(); return; }
  const W = 600, H = 220, M = 10;
  const min = Math.min(...serie.map((p) => p.saldo)), max = Math.max(...serie.map((p) => p.saldo));
  const rango = max - min || 1;
  const x = (i) => M + (i / (serie.length - 1)) * (W - 2 * M);
  const y = (v) => H - M - ((v - min) / rango) * (H - 2 * M);
  const pts = serie.map((p, i) => `${x(i).toFixed(1)},${y(p.saldo).toFixed(1)}`).join(" ");
  const sube = serie[serie.length - 1].saldo >= serie[0].saldo;
  const NS = "http://www.w3.org/2000/svg";
  const mk = (tag, attrs) => {
    const el = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
    return el;
  };
  svg.replaceChildren(
    mk("line", { x1: M, y1: y(serie[0].saldo), x2: W - M, y2: y(serie[0].saldo), stroke: "var(--muted)", "stroke-dasharray": "4 4" }),
    mk("polyline", { points: pts, fill: "none", stroke: sube ? "var(--verde)" : "var(--rojo)", "stroke-width": 2 }),
  );
}

// ─ Tabla de operaciones ───────────
function listaFiltrada() {
  let lista = trades.map((t, i) => ({ ...t, idx: i }));
  if (filtros.activo !== "Todos") lista = lista.filter((t) => t.asset === filtros.activo);
  if (filtros.texto) {
    const q = filtros.texto.toLowerCase();
    lista = lista.filter((t) => `${t.asset} ${t.strategy} ${t.timestamp}`.toLowerCase().includes(q));
  }
  lista.sort((a, b) => {
    const campo = filtros.orden;
    const va = campo === "idx" ? a.idx : a[campo];
    const vb = campo === "idx" ? b.idx : b[campo];
    if (typeof va === "string") return filtros.asc ? va.localeCompare(vb) : vb.localeCompare(va);
    return filtros.asc ? va - vb : vb - va;
  });
  return lista;
}

function pintarTabla() {
  const cuerpo = $("cuerpo-trades");
  if (!cuerpo) return;
  const lista = listaFiltrada();
  const vacio = $("trades-vacio");
  if (vacio) vacio.hidden = lista.length > 0;
  $("tabla-trades").hidden = lista.length === 0;

  cuerpo.replaceChildren(...lista.map((t) => {
    const tr = document.createElement("tr");
    tr.dataset.idx = t.idx;
    const td = (texto, clase = "") => {
      const c = document.createElement("td");
      c.textContent = texto;
      if (clase) c.className = clase;
      tr.appendChild(c);
    };
    td(t.timestamp);
    td(t.asset);
    td(t.direction === 1 ? "CALL ▲" : "PUT ▼", t.direction === 1 ? "positivo" : "negativo");
    td(t.strategy);
    td(t.won ? "GANADA" : "PERDIDA", t.won ? "positivo" : "negativo");
    td(fmtCop(t.profit, true), "num " + (t.won ? "positivo" : "negativo"));
    td(t.ai || "—");
    return tr;
  }));
}

function pintarSelectorActivos() {
  const sel = $("filtro-activo");
  if (!sel) return;
  const actuales = new Set(trades.map((t) => t.asset));
  const valor = filtros.activo;
  sel.replaceChildren(
    ...["Todos", ...[...actuales].sort()].map((a) => {
      const o = document.createElement("option");
      o.value = a; o.textContent = a;
      return o;
    }),
  );
  sel.value = actuales.has(valor) || valor === "Todos" ? valor : "Todos";
  filtros.activo = sel.value;
}

// ─ Modal de inspección técnica (snapshot de velas) ───────────────
function abrirModal(idx) {
  const t = trades[idx];
  if (!t) return;
  const modal = $("modal-trade");
  $("modal-titulo").textContent = `Inspección: ${t.asset} · ${t.timestamp}`;
  $("modal-info").textContent = `Estrategia: ${t.strategy} · Dirección: ${t.direction === 1 ? "CALL ▲" : "PUT ▼"} · Entrada: ${t.indicators?.price ?? "—"} · IA: ${t.ai || "sin filtro"} (${t.indicators?.reason || "—"})`;
  pintarSnapshot(t.snapshot || [], t.won);
  modal.showModal();
}

function pintarSnapshot(velas, won) {
  const svg = $("modal-snapshot");
  if (!svg || velas.length < 2) { svg.replaceChildren(); return; }
  const W = 520, H = 160, M = 8;
  const cierres = velas.map((v) => v.close);
  const min = Math.min(...cierres), max = Math.max(...cierres);
  const rango = max - min || 1;
  const x = (i) => M + (i / (cierres.length - 1)) * (W - 2 * M);
  const y = (v) => H - M - ((v - min) / rango) * (H - 2 * M);
  const pts = cierres.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const NS = "http://www.w3.org/2000/svg";
  const mk = (tag, attrs) => {
    const el = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
    return el;
  };
  svg.replaceChildren(mk("polyline", { points: pts, fill: "none", stroke: won ? "var(--verde)" : "var(--rojo)", "stroke-width": 2 }));
}

// ─ Exportar CSV ────────────
function exportarCSV() {
  const filas = [["Hora", "Activo", "Dirección", "Estrategia", "Resultado", "Ganancia(COP)", "Saldo", "IA"],
  ...listaFiltrada().map((t) => [t.timestamp, t.asset, t.direction === 1 ? "CALL" : "PUT", t.strategy,
  t.won ? "GANADA" : "PERDIDA", Math.round(t.profit), Math.round(t.balance), t.ai || ""])];
  const csv = filas.map((f) => f.map((v) => `"${String(v).replace(/"/g, '""')}"`).join(";")).join("\n");
  const blob = new Blob(["\ufeff" + csv], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "operaciones-tradinglab.csv";
  a.click();
  URL.revokeObjectURL(a.href);
}

// ─ Registro (log) ──────────
function agregarLog(texto) {
  const log = $("motor-log");
  if (!log) return;
  const linea = document.createElement("div");
  linea.textContent = `[${new Date().toLocaleTimeString("es-CO")}] ${texto}`;
  log.prepend(linea);
  while (log.children.length > 200) log.lastChild.remove();
}

function ponerBadge(estado, texto) {
  const b = $("motor-badge");
  if (!b) return;
  b.textContent = "● " + (texto || estado);
  b.className = "badge " + (
    estado === "CONNECTED" ? "badge-verde" :
      estado === "RECONNECTING" ? "badge-amarillo" :
        estado === "TRADE_IN_PROGRESS" ? "badge-azul" : "badge-rojo");
}

// ─ Sesión: iniciar / parar (dos modos) ─────────────
async function iniciar() {
  const cfg = JSON.parse(localStorage.getItem("tradinglab.motor.cfg") || "null") || {
    sim: true, assets: ["EURUSD"], amount: 5000, expiry: 1, max_trades: 30,
    strategy: "auto", ai: "none", max_loss: 50000, max_streak: 4,
    start_hour: 0, end_hour: 24, risk_reduction_pct: 50,
  };
  try {
    await iniciarSesion(cfg);
    ponerBadge("RUNNING", "Operando...");
    agregarLog("▶ Sesión iniciada");
  } catch (e) {
    agregarLog(`✖ No se pudo iniciar: ${e.message}`);
  }
}

// ─ Cables ──────────
function cablear() {
  // Filtros de la tabla.
  $("filtro-activo")?.addEventListener("change", (e) => { filtros.activo = e.target.value; guardarLocal(); pintarTabla(); });
  $("filtro-texto")?.addEventListener("input", (e) => { filtros.texto = e.target.value; guardarLocal(); pintarTabla(); });
  document.querySelector("#tabla-trades thead")?.addEventListener("click", (e) => {
    const th = e.target.closest("th[data-orden]");
    if (!th) return;
    if (filtros.orden === th.dataset.orden) filtros.asc = !filtros.asc;
    else filtros = { ...filtros, orden: th.dataset.orden, asc: false };
    guardarLocal(); pintarTabla();
  });
  $("boton-exportar-csv")?.addEventListener("click", exportarCSV);

  // Modal al hacer clic en una fila.
  $("cuerpo-trades")?.addEventListener("click", (e) => {
    const tr = e.target.closest("tr[data-idx]");
    if (tr) abrirModal(Number(tr.dataset.idx));
  });
  $("modal-cerrar")?.addEventListener("click", () => $("modal-trade").close());

  // Control de sesión.
  $("boton-iniciar")?.addEventListener("click", iniciar);
  $("boton-parar-now")?.addEventListener("click", async () => { await pararSesion("now"); agregarLog("■ Parada inmediata solicitada"); });
  $("boton-parar-soft")?.addEventListener("click", async () => { await pararSesion("soft"); agregarLog("⏸ Parada suave: cerrará tras las operaciones abiertas"); });
}

// ─ Eventos del motor ────────────────
function escuchar() {
  onMotor("init", (m) => {
    trades = m.trades || [];
    curvaEquity = trades.map((t, i) => ({ t: i, saldo: t.balance }));
    pintarSelectorActivos(); pintarKPIs(); pintarTabla();
    ponerBadge(m.activo ? "RUNNING" : "IDLE", m.activo ? "Operando..." : "Detenido");
    agregarLog(`Sesión recuperada: ${trades.length} operaciones previas`);
  });
  onMotor("trade", (t) => {
    trades.push(t);
    curvaEquity.push({ t: trades.length, saldo: t.balance });
    pintarSelectorActivos(); pintarKPIs(); pintarTabla();
    agregarLog(`${t.won ? "✔" : "✖"} ${t.asset} ${t.direction === 1 ? "CALL" : "PUT"} → ${fmtCop(t.profit, true)}`);
  });
  onMotor("balance", (b) => {
    const el = $("kpi-balance");
    if (el) el.textContent = fmtCop(b.value);
  });
  onMotor("log", (l) => agregarLog(l.text));
  onMotor("badge_status", (b) => ponerBadge(b.state, b.text));
  onMotor("conn_status", (c) => {
    if (c.state === "CONNECTED") ponerBadge("CONNECTED", "Motor conectado");
  });
  onMotor("session_done", () => {
    ponerBadge("IDLE", "Detenido");
    agregarLog("■ Sesión finalizada");
  });
}

// ─ Arranque público ────────────────
let iniciado = false;
export function iniciarDashboard() {
  if (iniciado) return;
  iniciado = true;
  cargarLocal();
  cablear();
  escuchar();   // ← antes de conectar: los primeros mensajes del WS no se pierden
  conectarMotor();
  // Fallback: si el WebSocket tarda, recupera el estado por HTTP.
  setTimeout(async () => {
    if (!trades.length) {
      const est = await recuperarEstado();
      if (est && est.trades.length) {
        trades = est.trades;
        curvaEquity = trades.map((t, i) => ({ t: i, saldo: t.balance }));
        pintarSelectorActivos(); pintarKPIs(); pintarTabla();
        ponerBadge(est.activo ? "RUNNING" : "IDLE", est.activo ? "Operando..." : "Detenido");
      }
    }
  }, 2500);
}
