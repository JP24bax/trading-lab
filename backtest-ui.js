// backtest-ui.js — Pestaña "Backtest": las 92 estrategias del motor, evaluadas con walk-forward.
// Consume /backtest/search (elige en una ventana, opera la siguiente, p-valores ajustados) y
// /backtest/single (una estrategia sobre todo el histórico, p ajustado por 92 comparaciones).
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

// ─ Parámetros comunes (datos, filtros) ─────────────
function params() {
  return {
    asset: $("bt-activo").value.trim().toUpperCase() || "EURUSD",
    history: Math.max(600, Number($("bt-historico").value) || 10000),
    expiry: Number($("bt-expiracion").value) || 1,
    sim: $("bt-fuente").value === "sim",
    filtro_volatilidad: $("bt-f-vol").checked,
    filtro_tendencia: $("bt-f-tend").checked,
    ...credenciales(),
  };
}

// Credenciales: las que se guardaron en la pestaña Config (si no hay, el motor usa sus variables de entorno).
function credenciales() {
  try {
    const c = JSON.parse(localStorage.getItem("tradinglab.motor.cfg") || "null") || {};
    return c.email && c.password ? { email: c.email, password: c.password } : {};
  } catch { return {}; }
}

function pintarAviso(datos) {
  const el = $("bt-aviso-datos");
  if (!el) return;
  el.hidden = !datos;
  if (!datos) return;
  el.textContent = (datos.sintetico ? "⚠ " : "ℹ ") + datos.aviso;
  el.className = "aviso-datos" + (datos.sintetico ? " sint" : "");
}

function explicarError(e) {
  const m = String(e.message || e);
  return /login|correo|contraseña/i.test(m)
    ? `${m}. Guarda tu correo y contraseña de IQ Option en la pestaña Config, o define IQ_EMAIL e IQ_PASSWORD en el motor.`
    : m;
}

// ─ Métricas resumidas ─────────────
function pintarMetricas(res, payout, extra = "") {
  const el = $("bt-metricas");
  if (!el) return;
  const pct = (v, d = 1) => (v * 100).toLocaleString("es-CO", { maximumFractionDigits: d });
  const equilibrio = 1 / (1 + payout);
  el.hidden = false;
  el.textContent =
    `Operaciones: ${res.n ?? 0} (empates ${res.ties ?? 0}) · Win rate: ${pct(res.winrate ?? 0)}% ` +
    `(equilibrio ${pct(equilibrio)}% con payout ${pct(payout, 0)}%) · p=${(res.p ?? 1).toFixed(3)} · ` +
    `Profit Factor: ${(res.profit_factor ?? 0).toFixed(2)} · Racha máx. negativa: ${res.max_loss_streak ?? 0}` +
    (extra ? ` · ${extra}` : "");
}

const colorNivel = (n) => (n === "bad" ? "var(--rojo)" : n === "good" ? "var(--verde)" : "#f59e0b");

// ─ Búsqueda masiva (walk-forward) ────────────
async function busquedaMasiva() {
  const p = params();
  const boton = $("bt-buscar");
  boton.disabled = true;
  $("bt-estado").textContent = `⏳ ${p.sim ? "Generando" : "Descargando"} ${p.history} velas de ${p.asset} y evaluando las 92 estrategias en ventanas...`;
  try {
    const r = await backtestBusqueda(p);
    pintarAviso(r.datos);
    pintarVeredicto(r.veredicto);
    const w = r.walk || {};
    pintarMetricas(r.test, r.payout,
      `ventanas operadas ${w.ventanas_operadas ?? 0}/${w.ventanas_total ?? 0}, rentables ${w.ventanas_positivas ?? 0}`);
    const el = $("bt-elegida");
    el.hidden = false;
    el.textContent = r.elegida
      ? `Candidata de hoy: ${r.elegida} — ${r.descripcion || ""} (mejor en las velas más recientes; su rendimiento futuro NO está validado: el veredicto de arriba juzga el método).`
      : "Ninguna estrategia reunió operaciones suficientes en las velas recientes.";
    pintarComparacion(r);
    pintarSerie($("bt-curva"), r.equity_curve || [], colorNivel(r.veredicto.nivel));
    pintarFolds(w.folds || []);
    pintarTop10(r.top10 || []);
    $("bt-estado").textContent = `✔ Evaluación completada: ${r.tested} estrategias, ${r.candidates} con muestra suficiente. ` +
      `Payout usado ${(r.payout * 100).toFixed(0)}% (el del bróker era ${(r.payout_broker * 100).toFixed(0)}%).`;
  } catch (e) {
    $("bt-estado").textContent = `✖ Error en la búsqueda: ${explicarError(e)}`;
  } finally {
    boton.disabled = false;
  }
}

function pintarComparacion(r) {
  const el = $("bt-comparacion");
  if (!el) return;
  if (!r.sin_filtros || !r.filtros) { el.hidden = true; return; }
  const f = r.test, s = r.sin_filtros;
  const pct = (v) => ((v ?? 0) * 100).toFixed(1) + "%";
  el.hidden = false;
  el.textContent =
    `Con filtros: ${f.n} ops, win rate ${pct(f.winrate)}, EV ${(f.ev ?? 0).toFixed(3)}/op · ` +
    `Sin filtros: ${s.n} ops, win rate ${pct(s.winrate)}, EV ${(s.ev ?? 0).toFixed(3)}/op. ` +
    `Con tan pocas operaciones una diferencia de 1-3 puntos es ruido; solo cuenta si se repite en datos reales y muestras grandes.`;
}

// ─ Ventanas del walk-forward ──────────
function pintarFolds(filas) {
  const tabla = $("bt-folds-tabla");
  if (!tabla) return;
  tabla.hidden = filas.length === 0;
  $("bt-folds-vacio").hidden = filas.length > 0;
  const pct = (v) => (v == null ? "—" : (v * 100).toFixed(1) + "%");
  $("bt-folds-cuerpo").replaceChildren(...filas.map((f) => {
    const tr = document.createElement("tr");
    const td = (texto, clase = "") => {
      const c = document.createElement("td");
      c.textContent = texto;
      if (clase) c.className = clase;
      tr.appendChild(c);
    };
    td(String(f.ventana), "num");
    td(f.estrategia ? (f.opero ? f.estrategia : `${f.estrategia} (no superó el equilibrio: no opera)`) : "sin muestra suficiente");
    td(pct(f.train_wr), "num");
    td(String(f.test_n ?? 0), "num");
    td(f.opero ? pct(f.test_wr) : "—", "num");
    return tr;
  }));
}

// ─ Ranking en tabla ──────────
function pintarTop10(filas) {
  const tabla = $("bt-top-tabla");
  if (!tabla) return;
  tabla.hidden = filas.length === 0;
  $("bt-top-vacio").hidden = filas.length > 0;
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
    td(String(f.n ?? 0), "num");
    td(((f.winrate ?? 0) * 100).toFixed(1) + "%", "num");
    td((f.z ?? 0).toFixed(2), "num");
    td((f.p ?? 1).toFixed(3), "num");
    td((f.p_ajustado ?? 1).toFixed(3), "num");
    td(`${f.tramos_positivos ?? 0}/${f.tramos ?? 0}`, "num");
    return tr;
  }));
}

// ─ Probar una estrategia concreta ───────────
async function probarSeleccionada() {
  const nombre = $("bt-estrategia").value;
  if (!nombre) { $("bt-estado").textContent = "Elige una estrategia del desplegable."; return; }
  const p = params();
  const boton = $("bt-probar");
  boton.disabled = true;
  $("bt-estado").textContent = `⏳ Probando «${nombre}» en ${p.history} velas...`;
  try {
    const r = await backtestSingle(nombre, p);
    pintarAviso(r.datos);
    pintarVeredicto(r.veredicto);
    pintarMetricas(r.res, r.payout,
      `p ajustado ×92 = ${(r.p_ajustado ?? 1).toFixed(3)} · ventanas rentables ${r.ventanas.positivas}/${r.ventanas.total} · «${r.estrategia}» — ${r.descripcion || ""}`);
    $("bt-comparacion").hidden = true;
    pintarSerie($("bt-curva"), r.equity_curve || [], colorNivel(r.veredicto.nivel));
    $("bt-estado").textContent = "✔ Backtest individual completado.";
  } catch (e) {
    $("bt-estado").textContent = `✖ Error: ${explicarError(e)}`;
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
