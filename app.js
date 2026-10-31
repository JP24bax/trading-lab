// TradingLab Â· LÃ³gica de la app (v2 Â· motor binario IQ Option).
// El nÃºcleo cuantitativo (indicadores, backtest de payoff fijo, exportadores) vive en motor-binario.js.
import {
  generarEstrategia, backtest, aJSON, aPython, enviarSenal,
} from "./motor-binario.js";
import { iniciarDashboard } from "./dashboard.js";
import { iniciarBacktest } from "./backtest-ui.js";
import { iniciarConfig } from "./config-ui.js";
import { iniciarAnalisis } from "./analisis-ui.js";
import { iniciarReglas } from "./reglas-ui.js";

// Atajo para coger elementos del HTML por su id.
const $ = (id) => document.getElementById(id);

// â”€â”€ Estado â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
const CLAVE_GUARDADO = "tradinglab.v2";
const UMBRAL_WR = 58;   // descarta estrategias con Win Rate menor
const PAYOUT = 0.82;    // payout tÃ­pico de IQ Option en pares mayores

let estado = {
  estrategias: [],
  seleccionada: null,
  capital: 1000,
  tamanoLote: 10,
  webhook: { url: "", token: "" },
  historial: [],  // últimas señales enviadas (máx. 20)
  ultimaSenal: null,
};

function guardar() {
  try { localStorage.setItem(CLAVE_GUARDADO, JSON.stringify(estado)); }
  catch (e) { console.warn("No se pudo guardar en el navegador:", e); }
}

function cargar() {
  try {
    const texto = localStorage.getItem(CLAVE_GUARDADO);
    if (texto) estado = { ...estado, ...JSON.parse(texto) };
  } catch (e) {
    console.warn("Datos guardados ilegibles; empezamos de cero.", e);
  }
}

// replaceChildren no acepta null/false ni aplanar listas: usamos esta ayuda.
function vaciarYAniadir(padre, hijos) {
  const lista = [];
  for (const h of hijos.flat(Infinity)) if (h) lista.push(h);
  padre.replaceChildren(...lista);
}

// â”€â”€ Formato de cifras â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
function fmtDinero(n) {
  const firma = n >= 0 ? "+" : "";
  return firma + n.toLocaleString("es-ES", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
}
function fmtNum(n, dec = 1) {
  return n.toLocaleString("es-ES", { minimumFractionDigits: dec, maximumFractionDigits: dec });
}

// â”€â”€ Mercado simulado (determinista, 1200 velas de 1 min) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
// Dos tercios para el entrenamiento (in-sample), el tercio final para
// la validaciÃ³n out-of-sample (OOS) del filtro de robustez.
let serieMercado = { velas: [], cierres: [] };

function crearMercado() {
  let x = 20260101;
  const azar = () => {
    x ^= x << 13; x ^= x >>> 17; x ^= x << 5;
    return (x >>> 0) / 4294967296;
  };
  const velas = [];
  let precio = 1.1;
  let tendencia = 0;
  for (let i = 0; i < 1200; i++) {
    if (i % 80 === 0) tendencia = (azar() - 0.45) * 0.00008;
    const apertura = precio;
    const cierre = Math.max(0.5, apertura * (1 + tendencia + (azar() - 0.5) * 0.0006));
    velas.push({
      apertura, cierre,
      maximo: Math.max(apertura, cierre) * (1 + azar() * 0.0002),
      minimo: Math.min(apertura, cierre) * (1 - azar() * 0.0002),
    });
    precio = cierre;
  }
  serieMercado = { velas, cierres: velas.map((v) => v.cierre) };
}

// â”€â”€ GeneraciÃ³n + filtro genÃ©tico por Win Rate â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
function evaluar(e) {
  const r = backtest(e, serieMercado.velas, { payout: PAYOUT });
  Object.assign(e, r);
  e.evaluada = true;
  return r;
}

function intentarGenerar(semilla) {
  const e = generarEstrategia(semilla);
  evaluar(e);
  // Filtro: Win Rate â‰¥ 58% (esperanza positiva con payout 82%) y suficientes ops.
  if (e.acierto < UMBRAL_WR || e.operaciones < 10) return null;
  return e;
}

async function generarLote(n) {
  const pedidos = n ?? estado.tamanoLote;
  const nuevos = [];
  const t0 = performance.now();
  const PRESUPUESTO_MS = 25000;   // cede el navegador cada 20 intentos y para a los 8 s
  let intentos = 0;
  while (nuevos.length < pedidos && performance.now() - t0 < PRESUPUESTO_MS) {
    // Ráfaga sincrónica y luego cede: la interfaz no se congela.
    for (let k = 0; k < 20 && nuevos.length < pedidos; k++) {
      const semilla = 1 + Math.floor(Math.random() * 9999999);
      const e = intentarGenerar(semilla);
      if (e) nuevos.push(e);
      intentos++;
    }
    await new Promise((r) => setTimeout(r, 0));
  }
  const estadoGen = document.getElementById('estado-generador');
  if (nuevos.length === 0) {
    if (estadoGen) estadoGen.textContent = `Ninguna de las ${intentos} candidatas superó el ${UMBRAL_WR}% de Win Rate. Reintenta: la búsqueda es estocástica.`;
    return;
  }
  if (estadoGen) estadoGen.textContent = `${nuevos.length} estrategias aceptadas tras ${intentos} intentos.`;
  estado.estrategias.push(...nuevos);
  estado.estrategias.sort((a, b) => b.acierto - a.acierto);
  guardar();
  actualizarTodo();
  console.log(`Aceptadas ${nuevos.length} estrategias tras ${intentos} intentos (${Math.round(performance.now() - t0)} ms).`);
}

// â”€â”€ Ranking â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
let ordenActual = { campo: "acierto", descendente: true };

function pintarRanking() {
  const hay = estado.estrategias.length > 0;
  $("ranking-vacio").hidden = hay;
  $("tabla-ranking").hidden = !hay;
  if (!hay) return;

  const lista = [...estado.estrategias].sort((a, b) =>
    ordenActual.descendente ? b[ordenActual.campo] - a[ordenActual.campo] : a[ordenActual.campo] - b[ordenActual.campo]);

  vaciarYAniadir($("cuerpo-ranking"), lista.map((e, i) => {
    const fila = document.createElement("tr");
    fila.dataset.id = e.id;
    if (e.id === estado.seleccionada) fila.classList.add("seleccionada");
    const td = (texto, clase = "") => {
      const c = document.createElement("td");
      c.textContent = texto;
      if (clase) c.className = clase;
      fila.appendChild(c);
      return c;
    };
    td(String(i + 1));
    td(e.nombre);
    td(fmtNum(e.acierto, 1) + " %", "num" + (e.acierto >= UMBRAL_WR ? " positivo" : ""));
    td(fmtDinero(e.beneficio), "num " + (e.beneficio >= 0 ? "positivo" : "negativo"));
    td(String(e.operaciones), "num");
    td(`${e.expiracion}m Â· ${e.gestion.tipo_operacion}`, "num");
    return fila;
  }));
}

document.querySelector("#tabla-ranking thead").addEventListener("click", (ev) => {
  const th = ev.target.closest("th[data-orden]");
  if (!th) return;
  const campo = th.dataset.orden;
  if (ordenActual.campo === campo) ordenActual.descendente = !ordenActual.descendente;
  else ordenActual = { campo, descendente: true };
  pintarRanking();
});

$("cuerpo-ranking").addEventListener("click", (ev) => {
  const fila = ev.target.closest("tr[data-id]");
  if (!fila) return;
  estado.seleccionada = fila.dataset.id;
  guardar();
  actualizarTodo();
});

// â”€â”€ Probador â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
function pintarGraficaCapital(ruta) {
  const svg = $("grafica-capital");
  const ancho = 600, alto = 220, margen = 8;
  if (!ruta || ruta.length < 2) { vaciarYAniadir(svg, []); return; }
  const min = Math.min(...ruta), max = Math.max(...ruta);
  const rango = max - min || 1;
  const x = (i) => margen + (i / (ruta.length - 1)) * (ancho - 2 * margen);
  const y = (v) => alto - margen - ((v - min) / rango) * (alto - 2 * margen);
  const puntos = ruta.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const sube = ruta[ruta.length - 1] >= ruta[0];
  vaciarYAniadir(svg, [
    crearSVG("line", { x1: margen, y1: y(ruta[0]), x2: ancho - margen, y2: y(ruta[0]), stroke: "var(--muted)", "stroke-dasharray": "4 4", "stroke-width": 1 }),
    crearSVG("polyline", { points: puntos, fill: "none", stroke: sube ? "var(--verde)" : "var(--rojo)", "stroke-width": 2 }),
  ]);
}

function crearSVG(etiqueta, atributos) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", etiqueta);
  for (const [k, v] of Object.entries(atributos)) el.setAttribute(k, v);
  return el;
}

function pintarProbador() {
  const e = estado.estrategias.find((x) => x.id === estado.seleccionada);
  $("probador-vacio").hidden = Boolean(e);
  $("probador-contenido").hidden = !e;
  if (!e) return;
  evaluar(e);
  guardar();
  $("prob-nombre").textContent = e.nombre;
  $("prob-entrada").textContent = `CALL: ${e.entrada_call.join(" Y ")} Â· PUT: ${e.entrada_put.join(" Y ")}`;
  $("prob-salida").textContent = `${e.activo} Â· expira ${e.expiracion} min Â· ${e.gestion.monto} USD Â· ${e.gestion.tipo_operacion}`;
  const ben = $("prob-beneficio");
  ben.textContent = fmtDinero(e.beneficio);
  ben.className = "cuadro-valor " + (e.beneficio >= 0 ? "positivo" : "negativo");
  $("prob-acierto").textContent = fmtNum(e.acierto, 1) + " %";
  $("prob-operaciones").textContent = String(e.operaciones);
  $("prob-drawdown").textContent = fmtDinero(e.drawdown);
  $("prob-drawdown").className = "cuadro-valor negativo";
  pintarGraficaCapital(e.ruta);

  // ValidaciÃ³n out-of-sample (tercio final del histÃ³rico, no visto en el filtro).
  const mitad = Math.floor(serieMercado.velas.length * 2 / 3);
  const oos = backtest(e, serieMercado.velas.slice(mitad), { payout: PAYOUT });
  const ok = oos.acierto >= UMBRAL_WR && oos.beneficio > 0;
  const celda = $("prob-oos");
  celda.textContent = `${ok ? "ROBUSTA" : "FRÃGIL"} Â· WR OOS ${fmtNum(oos.acierto, 1)}% Â· PnL ${fmtDinero(oos.beneficio)}`;
  celda.className = "cuadro-valor " + (ok ? "positivo" : "negativo");
  pintarRanking();
}

function actualizarTodo() {
  pintarRanking();
  pintarProbador();
  pintarExportar();
}

// â”€â”€ Exportar: JSON, Python bot y webhook â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
function pintarExportar() {
  const e = estado.estrategias.find((x) => x.id === estado.seleccionada);
  $("exportar-vacio").hidden = Boolean(e);
  $("exportar-contenido").hidden = !e;
  if (!e) return;
  $("exp-nombre").textContent = e.nombre;
  $("exp-codigo").textContent = aJSON(e);
  $("webhook-url").value = estado.webhook.url;
  $("webhook-token").value = estado.webhook.token;
}

function descargar(nombre, contenido, tipo = "text/plain;charset=utf-8") {
  const blob = new Blob([contenido], { type: tipo });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = nombre;
  a.click();
  URL.revokeObjectURL(url);
}

function nombreArchivo(e, ext) { return e.nombre.toLowerCase().replace(/\s+/g, "-") + ext; }

async function copiar(texto, boton) {
  try {
    await navigator.clipboard.writeText(texto);
    const t = boton.textContent;
    boton.textContent = "Â¡Copiado!";
    setTimeout(() => { boton.textContent = t; }, 1500);
  } catch {
    alert("No se pudo copiar. Selecciona el texto y usa Ctrl+C.");
  }
}

// â”€â”€ NavegaciÃ³n â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
function mostrarZona(nombre) {
  for (const seccion of document.querySelectorAll(".zona")) {
    seccion.hidden = seccion.id !== "zona-" + nombre;
  }
  for (const boton of document.querySelectorAll(".pestana")) {
    boton.classList.toggle("activa", boton.dataset.zona === nombre);
  }
}

for (const boton of document.querySelectorAll(".pestana")) {
  boton.addEventListener("click", () => mostrarZona(boton.dataset.zona));
}

// â”€â”€ Arranque â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
cargar();
crearMercado();
$("boton-generar").addEventListener("click", () => generarLote());
$("boton-generar-masivo").addEventListener("click", () => generarLote(50));
$("boton-reprobar").addEventListener("click", () => {
  const v = Number($("campo-capital").value);
  if (Number.isFinite(v) && v > 0) { estado.capital = v; guardar(); }
  pintarProbador();
});
$("boton-descargar-json").addEventListener("click", () => {
  const e = estado.estrategias.find((x) => x.id === estado.seleccionada);
  if (e) descargar(nombreArchivo(e, ".json"), aJSON(e), "application/json");
});
$("boton-descargar-python").addEventListener("click", () => {
  const e = estado.estrategias.find((x) => x.id === estado.seleccionada);
  if (e) descargar(nombreArchivo(e, ".py"), aPython(e), "text/x-python");
});
$("boton-copiar-json").addEventListener("click", () => {
  const e = estado.estrategias.find((x) => x.id === estado.seleccionada);
  if (e) copiar(aJSON(e), $("boton-copiar-json"));
});
$("boton-webhook-guardar").addEventListener("click", () => {
  estado.webhook.url = $("webhook-url").value.trim();
  estado.webhook.token = $("webhook-token").value.trim();
  guardar();
  mostrarResultadoWebhook("Configuración guardada.", "exito");});
$("boton-borrar-historial").addEventListener("click", () => {
  estado.historial = [];
  guardar();
  pintarHistorial();
});
$("boton-webhook-probar").addEventListener("click", async () => {
  const e = estado.estrategias.find((x) => x.id === estado.seleccionada);
  if (!e) return;
  const url = $("webhook-url").value.trim();
  const token = $("webhook-token").value.trim();
  if (!url || !token) {
    mostrarResultadoWebhook("Configura primero la URL y el token del backend.", "error");
    return;
  }
  const boton = $("boton-webhook-probar");
  boton.disabled = true;
  mostrarResultadoWebhook("Enviando señal al backend...", "");
  try {
    const r = await enviarSenal(url, token, e, "CALL");
    const ok = r.status === "SIMULATED" || r.status === "SUCCESS";
    const titulo = ok ? "\u2705 " + r.status : "\u274c " + (r.detail || r.status || "Respuesta no v\u00e1lida");
    registrarEnHistorial(e, "CALL", ok ? r.status : "ERROR");
    mostrarResultadoWebhook(
      `${titulo}\n${JSON.stringify(r, null, 2)}`,
      ok ? "exito" : "error",
    );
  } catch (err) {
    mostrarResultadoWebhook(`\u274c Error contactando el backend: ${err}`, "error");
    registrarEnHistorial(e, "CALL", "SIN_CONEXION");
  } finally {
    boton.disabled = false;
  }
});

// Registra una señal enviada en el historial (máximo 20, persistente).
function registrarEnHistorial(estrategia, accion, estadoFinal) {
  estado.historial.unshift({
    hora: new Date().toLocaleTimeString("es-ES"),
    par: estrategia.activo,
    accion,
    monto: estrategia.gestion.monto,
    exp: estrategia.expiracion,
    estado: estadoFinal,
  });
  if (estado.historial.length > 20) estado.historial.length = 20;
  guardar();
  pintarHistorial();
}

// Pinta la tabla del historial de señales.
function pintarHistorial() {
  const panel = $("historial-senales");
  if (!panel) return;
  panel.hidden = estado.historial.length === 0;
  const cuerpo = $("historial-cuerpo");
  if (!cuerpo) return;
  const fila = (h) => {
    const tr = document.createElement("tr");
    const td = (texto, clase = "") => {
      const c = document.createElement("td");
      c.textContent = texto;
      if (clase) c.className = clase;
      tr.appendChild(c);
    };
    td(h.hora);
    td(h.par);
    td(h.accion, h.accion === "CALL" ? "positivo" : "negativo");
    td(String(h.monto), "num");
    td(h.exp + "m", "num");
    td(h.estado, h.estado === "ERROR" || h.estado === "SIN_CONEXION" ? "negativo" : "positivo");
    return tr;
  };
  cuerpo.replaceChildren(...estado.historial.map(fila));
}

// Muestra el resultado del webhook en la página (sin alert).
function mostrarResultadoWebhook(texto, clase = "") {
  const panel = $("webhook-resultado");
  if (!panel) return;
  panel.hidden = false;
  panel.textContent = texto;
  panel.className = "webhook-resultado" + (clase ? " " + clase : "");
}
$("num-lote").textContent = String(estado.tamanoLote);
pintarRanking();
pintarHistorial();
mostrarZona("laboratorio");
iniciarDashboard();
iniciarBacktest();
iniciarConfig();
iniciarAnalisis();
iniciarReglas();
console.log("TradingLab v2 (binarias IQ Option) arrancada.");
