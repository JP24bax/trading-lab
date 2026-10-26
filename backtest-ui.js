// backtest-ui.js — Pestaña "Backtest": las 92 estrategias del motor sobre histórico.
// Consume /backtest/search (búsqueda masiva con train/validation/test) y
// /backtest/single (una estrategia en el tramo de prueba 25% final).
import { backtestBusqueda, backtestSingle, listarEstrategias } from "./motor.js";

const $ = (id) => document.getElementById(id);

// ─ Veredicto con color según nivel ─────────────
function pintarVeredicto(v) {
  const el = $("bt-veredicto");
  if (!el) return;
  el.hidden = false;
  el.textContent = v.texto;
  el.className = "bt-veredicto " + (v.nivel === "good" ? "exito" : v.nivel === "bad" ? "error" : "warn");
}

// ─ Curva de equity reutilizable (SVG propio) ─────────────
export function pintarSerie(svg, serie, color) {
  if (!svg) return;
  if (!serie || serie.length < 2) { svg.replaceChildren(); return; }
  const W = 600, H = 220, M = 10;
  const min = Math.min(...serie), max = Math.max(...serie);
  const rango = max - min || 1;
  const x = (i) => M + (i / (serie.length - 1)) * (W - 2 * M);
  const y = (v) => H - M - ((v - min) / rango) * (H - 2 * M);
  const pts = serie.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const mk = (tag, attrs) => {
    const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
    return el;
  };
  svg.replaceChildren(
    mk("line", { x1: M, y1: y(0), x2: W - M, y2: y(0), stroke: "var(--muted)", "stroke-dasharray": "4 4" }),
    mk("polyline", { points: pts, fill: "none", stroke: color, "stroke-width": 2 }),
  );
}

// ─ Métricas resumidas ─────────────
function pintarMetricas(res, payout, extra = "") {
  const el = $("bt-metricas");
  if (!el) return;
  const wr = (res.winrate * 100).toLocaleString("es-CO", { maximumFractionDigits: 1 });
  const be = (payout / (1 + payout) * 100).toLocaleString("es-CO", { maximumFractionDigits: 1 });
  el.hidden = false;
  el.textContent =
    `Operaciones: ${res.n ?? 0} · Win Rate: ${wr}% (equilibrio ${be}% con payout ${Math.round(payout * 100)}%) · ` +
    `Profit Factor: ${(res.profit_factor ?? 0).toFixed(2)} · Racha máx. negativa: ${res.max_loss_streak ?? 0}` +
    (extra ? ` · ${extra}` : "");
}

// ─ Búsqueda masiva ────────────
async function busquedaMasiva() {
  const activo = $("bt-activo").value.trim().toUpperCase() || "EURUSD";
  const historico = Number($("bt-historico").value) || 5000;
  const expiracion = Number($("bt-expiracion").value) || 1;
  const boton = $("bt-buscar");
  boton.disabled = true;
  $("bt-estado").textContent = `⏳ Descargando ${historico} velas y evaluando las 92 estrategias...`;
  try {
    const r = await backtestBusqueda({ asset: activo, sim: true, history: historico, expiry: expiracion });
    pintarVeredicto(r.veredicto);
    pintarMetricas(r.test, r.payout, `evaluadas ${r.tested} estrategias sobre ${r.velas} velas`);
    $("bt-elegida").textContent = `Estrategia elegida: ${r.elegida} — ${r.descripcion || ""}`;
    pintarSerie($("bt-curva"), (r.equity_curve || []).length ? r.equity_curve : r.test.equity_curve,
      r.veredicto.nivel === "bad" ? "var(--rojo)" : r.veredicto.nivel === "good" ? "var(--verde)" : "#f59e0b");
    pintarTop10(r.top10 || []);
    $("bt-estado").textContent = `✔ Búsqueda completada: ${r.candidates} candidatas válidas de ${r.tested} probadas.`;
  } catch (e) {
    $("bt-estado").textContent = `✖ Error en la búsqueda: ${e.message}`;
  } finally {
    boton.disabled = false;
  }
}

// ─ Top 10 en tabla ──────────
function pintarTop10(filas) {
  const tabla = $("bt-top-tabla");
  if (!tabla) return;
  tabla.hidden = filas.length === 0;
  const cuerpo = $("bt-top-cuerpo");
  cuerpo.replaceChildren(...filas.map((f) => {
    const tr = document.createElement("tr");
    const td = (texto, clase = "") => {
      const c = document.createElement("td");
      c.textContent = texto;
      if (clase) c.className = clase;
      tr.appendChild(c);
    };
    td(f.strategy);
    td(String(f.tr_n ?? 0), "num");
    td(((f.tr_wr ?? 0) * 100).toFixed(1) + "%", "num");
    td(String(f.va_n ?? 0), "num");
    td(((f.va_wr ?? 0) * 100).toFixed(1) + "%", "num");
    return tr;
  }));
}

// ─ Probar una estrategia concreta ───────────
async function probarSeleccionada() {
  const nombre = $("bt-estrategia").value;
  if (!nombre) { $("bt-estado").textContent = "Elige una estrategia del desplegable."; return; }
  const activo = $("bt-activo").value.trim().toUpperCase() || "EURUSD";
  const historico = Number($("bt-historico").value) || 5000;
  const expiracion = Number($("bt-expiracion").value) || 1;
  const boton = $("bt-probar");
  boton.disabled = true;
  $("bt-estado").textContent = `⏳ Probando «${nombre}» en el tramo de prueba (25% final)...`;
  try {
    const r = await backtestSingle(nombre, { asset: activo, sim: true, history: historico, expiry: expiracion });
    pintarVeredicto(r.veredicto);
    pintarMetricas(r.res, 0.82, `«${r.estrategia}» — ${r.descripcion || ""}`);
    pintarSerie($("bt-curva"), r.equity_curve || [],
      r.veredicto.nivel === "bad" ? "var(--rojo)" : r.veredicto.nivel === "good" ? "var(--verde)" : "#f59e0b");
    $("bt-estado").textContent = "✔ Backtest individual completado.";
  } catch (e) {
    $("bt-estado").textContent = `✖ Error: ${e.message}`;
  } finally {
    boton.disabled = false;
  }
}

// ─ Desplegable con el catálogo del motor ───────────
async function cargarCatalogo() {
  try {
    const cat = await listarEstrategias();
    const sel = $("bt-estrategia");
    if (!sel) return;
    sel.replaceChildren(
      ...cat.estrategias.map((e) => {
        const o = document.createElement("option");
        o.value = e.nombre;
        o.textContent = `${e.nombre} — ${e.descripcion.slice(0, 60)}`;
        return o;
      }),
    );
  } catch {
    // El motor no está disponible ahora; el desplegable queda vacío.
  }
}

export function iniciarBacktest() {
  if (!$("bt-buscar")) return; // la pestaña no existe todavía
  $("bt-buscar").addEventListener("click", busquedaMasiva);
  $("bt-probar").addEventListener("click", probarSeleccionada);
  cargarCatalogo();
}
