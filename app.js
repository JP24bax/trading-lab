// TradingLab · Lógica de la app. Se ejecuta en el navegador cuando carga la página.
// Estado y guardado centralizados; las funciones del MVP se van añadiendo por etapas.

// Atajo para coger elementos del HTML por su id: $("boton-generar").
const $ = (id) => document.getElementById(id);

// ── Estado de la app ──────────────────────────────────────────────────────
// Se guarda en el navegador (localStorage) para sobrevivir a recargar la página.
const CLAVE_GUARDADO = "tradinglab.v1";

let estado = {
  estrategias: [],       // lista de estrategias generadas
  seleccionada: null,    // id de la estrategia marcada en el ranking
  capitalInicial: 10000, // preferencias
  tamanoLote: 10,
};

function guardar() {
  try {
    localStorage.setItem(CLAVE_GUARDADO, JSON.stringify(estado));
  } catch (e) {
    console.warn("No se pudo guardar en el navegador:", e);
  }
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
  for (const h of hijos.flat(Infinity)) {
    if (h) lista.push(h);
  }
  padre.replaceChildren(...lista);
}

// ── Generador de estrategias ────────────────────────────────────────
// Combina piezas al azar, como un generador de estrategias con sus bloques: un tipo de
// entrada, una salida y umbrales. Cada estrategia recuerda su semilla para
// poder reproducir sus resultados.
const INDICADORES = ["RSI", "Medias", "MACD", "Bollinger", "Momentum", "Ruptura"];
const OPERACIONES = ["cruce", "rebote", "ruptura"];
const LETRAS = ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J"];

function nombreAleatorio(azar) {
  const ind = INDICADORES[Math.floor(azar() * INDICADORES.length)];
  const op = OPERACIONES[Math.floor(azar() * OPERACIONES.length)];
  const letra = LETRAS[Math.floor(azar() * LETRAS.length)];
  return `${ind} ${op} ${letra}${Math.floor(azar() * 9) + 1}`;
}

function crearEstrategia(azar, semilla) {
  const p = () => Math.floor(azar() * 20) + 5; // parámetro entre 5 y 25
  // Reglas estructuradas que entiende el motor de backtest, de un generador
  // aparte para no alterar la reproducción de estrategias antiguas.
  const r = multiconjuntoAzar(semilla * 77 + 13);
  const tipo = Math.floor(r() * 3);          // 0 cruce de medias · 1 rebote RSI · 2 ruptura
  const periodo = Math.floor(r() * 16) + 5;  // períodos del indicador (5–20)
  const velasSalida = Math.floor(r() * 15) + 5; // venta máxima en velas
  const textosEntrada = [
    "Cruce de medias: comprar cuando el precio cruza por encima de su media",
    "Rebote RSI: comprar cuando el RSI queda sobrevendido",
    "Ruptura: comprar cuando el precio rompe el máximo de las últimas velas",
  ];
  const stopLoss = Math.floor(azar() * 5) + 1;      // % de pérdida máxima por operación
  const takeProfit = Math.floor(azar() * 10) + 2;   // % de beneficio objetivo
  return {
    id: crypto.randomUUID(),
    nombre: nombreAleatorio(azar),
    semilla,
    reglas: { tipo, periodo, velasSalida },
    entrada: `${textosEntrada[tipo]} (${periodo} períodos)`,
    salida: `Stop −${stopLoss}% · objetivo +${takeProfit}% · máx. ${velasSalida} velas`,
    stopLoss,
    takeProfit,
    // Métricas (las calcula el backtest real, etapa 3):
    evaluada: false,
    puntuacion: 0,
    beneficio: 0,
    drawdown: 0,
    acierto: 0,
  };
}

// Las estrategias guardadas antes de la etapa 3 no tienen `reglas`:
// las regeneramos desde su semilla (el generador es determinista).
function asegurarReglas(e) {
  if (e.reglas) return;
  const nueva = crearEstrategia(multiconjuntoAzar(e.semilla), e.semilla);
  e.reglas = nueva.reglas;
  e.nombre = nueva.nombre;
  e.entrada = nueva.entrada;
  e.salida = nueva.salida;
  e.stopLoss = nueva.stopLoss;
  e.takeProfit = nueva.takeProfit;
  e.evaluada = false;
}

// ── Indicadores técnicos ───────────────────────────────────────────────
// Media móvil simple de los últimos n cierres.
function mediaMovil(velas, i, n) {
  if (i < n - 1) return null;
  let suma = 0;
  for (let j = i - n + 1; j <= i; j++) suma += velas[j].cierre;
  return suma / n;
}

// RSI (Relative Strength Index) clásico de Wilder con suavizado simple.
function calcularRSI(velas, i, n) {
  if (i < n) return null;
  let subidas = 0, bajadas = 0;
  for (let j = i - n + 1; j <= i; j++) {
    const dif = velas[j].cierre - velas[j - 1].cierre;
    if (dif > 0) subidas += dif; else bajadas -= dif;
  }
  if (subidas + bajadas === 0) return 50;
  return 100 - (100 / (1 + subidas / bajadas));
}

// ── Motor de backtest ─────────────────────────────────────────────
// Recorre la serie de precios una vez y simula las operaciones de la estrategia.
// Devuelve métricas + la curva de capital para la gráfica.
function hacerBacktest(estrategia, opciones = {}) {
  const { velas } = serieMercado;
  const capitalInicial = opciones.capitalInicial ?? estado.capitalInicial;
  const r = estrategia.reglas;
  const ruta = [capitalInicial]; // curva de capital (valor tras cada velas)
  let capital = capitalInicial;
  let enOperacion = false;
  let entrada = 0;       // precio de entrada
  let velasEnOperacion = 0;
  let maximo = 0;        // cumbre del capital (para el drawdown)
  let drawdownMax = 0;
  let operaciones = 0, ganadoras = 0;
  let gananciaTotal = 0; // suma de % de las operaciones cerradas

  const maximoReciente = []; // máximos de las últimas velas, para la ruptura

  for (let i = 1; i < velas.length; i++) {
    const v = velas[i];
    const anterior = velas[i - 1];

    if (!enOperacion) {
      // Señales de entrada según el tipo de estrategia.
      let senal = false;
      if (r.tipo === 0) { // cruce de medias: precio cruza por encima de su media
        const m = mediaMovil(velas, i, r.periodo);
        if (m !== null && anterior.cierre <= m && v.cierre > m) senal = true;
      } else if (r.tipo === 1) { // rebote RSI: sobrevendido ayer, recuperado hoy
        const rsiAntes = calcularRSI(velas, i - 1, r.periodo);
        const rsiAhora = calcularRSI(velas, i, r.periodo);
        if (rsiAntes !== null && rsiAntes < 35 && rsiAhora >= 35) senal = true;
      } else { // ruptura: precio supera el máximo de las últimas n velas
        while (maximoReciente.length > r.periodo) maximoReciente.shift();
        maximoReciente.push(anterior.maximo);
        if (maximoReciente.length === r.periodo && v.cierre > Math.max(...maximoReciente)) senal = true;
      }

      if (senal) {
        enOperacion = true;
        entrada = v.cierre;
        velasEnOperacion = 0;
      }
    } else {
      velasEnOperacion++;
      const cambio = (v.cierre - entrada) / entrada;
      const stopAlcanzado = cambio <= -estrategia.stopLoss / 100;
      const objetivoAlcanzado = cambio >= estrategia.takeProfit / 100;
      const tiempoAgotado = velasEnOperacion >= r.velasSalida;
      if (stopAlcanzado || objetivoAlcanzado || tiempoAgotado) {
        enOperacion = false;
        operaciones++;
        const ganancia = cambio;
        gananciaTotal += ganancia;
        if (ganancia > 0) ganadoras++;
        capital = capital * (1 + ganancia);
        ruta.push(capital);
        if (capital > maximo) maximo = capital;
        const caida = (maximo - capital) / maximo;
        if (caida > drawdownMax) drawdownMax = caida;
      }
    }
  }

  const beneficio = capital - capitalInicial;
  const acierto = operaciones > 0 ? (ganadoras / operaciones) * 100 : 0;
  // Puntuación: beneficio relativo menos castigo por drawdown y % de acierto.
  const puntuacion = beneficio / capitalInicial * 100 - drawdownMax * 100 * 0.5 + acierto * 0.3;
  return {
    beneficio: Math.round(beneficio),
    drawdown: Math.round(drawdownMax * capitalInicial),
    drawdownPct: drawdownMax * 100,
    acierto: Math.round(acierto),
    operaciones,
    puntuacion: Math.round(puntuacion * 10) / 10,
    ruta,
    gananciaMedia: operaciones > 0 ? gananciaTotal / operaciones : 0,
  };
}

// Serie de mercado compartida (se crea una vez en el arranque).
let serieMercado = { velas: [], puntos: [] };

// Evalúa una estrategia con el backtest real y guarda sus métricas.
function evaluar(estrategia) {
  asegurarReglas(estrategia);
  const r = hacerBacktest(estrategia);
  estrategia.beneficio = r.beneficio;
  estrategia.drawdown = r.drawdown;
  estrategia.acierto = r.acierto;
  estrategia.puntuacion = r.puntuacion;
  estrategia.evaluada = true;
  return r;
}

// Genera un lote de estrategias nuevas y las añade al estado.
function generarLote() {
  const lote = [];
  for (let i = 0; i < estado.tamanoLote; i++) {
    const semilla = 1 + Math.floor(Math.random() * 999999);
    const e = crearEstrategia(multiconjuntoAzar(semilla), semilla);
    evaluar(e);
    lote.push(e);
  }
  estado.estrategias.push(...lote);
  guardar();
  pintarRanking();
}

// ── Serie de mercado simulada ──────────────────────────────
// Paseo aleatorio con pequeñas tendencias: 400 velas OHLC. Determinista
// (misma serie en cada visita) para que los resultados sean repetibles.
function crearSerieMercado() {
  const azar = multiconjuntoAzar(20260101);
  const N = 400;
  const velas = [];
  let precio = 100;
  let tendencia = 0;
  for (let i = 0; i < N; i++) {
    // La tendencia cambia de vez en cuando, como en un mercado real.
    if (i % 40 === 0) tendencia = (azar() - 0.45) * 0.004;
    const apertura = precio;
    const cierre = Math.max(1, apertura * (1 + tendencia + (azar() - 0.5) * 0.04));
    const maximo = Math.max(apertura, cierre) * (1 + azar() * 0.015);
    const minimo = Math.min(apertura, cierre) * (1 - azar() * 0.015);
    velas.push({ apertura, cierre, maximo, minimo });
    precio = cierre;
  }
  return { velas, puntos: velas.map((v) => v.cierre) };
}
function multiconjuntoAzar(semilla) {
  let x = semilla >>> 0 || 1;
  return () => {
    x ^= x << 13; x ^= x >>> 17; x ^= x << 5;
    return (x >>> 0) / 4294967296;
  };
}

// ── Formato de cifras (estilo terminal) ─────────────────────────────────
function fmtDinero(n) {
  const firma = n >= 0 ? "+" : "";
  return firma + n.toLocaleString("es-ES", { style: "currency", currency: "EUR", maximumFractionDigits: 0 });
}

function fmtNum(n, dec = 1) {
  return n.toLocaleString("es-ES", { minimumFractionDigits: dec, maximumFractionDigits: dec });
}

// ── Ranking ────────────────────────────────────────────────────────────────
let ordenActual = { campo: "puntuacion", descendente: true };

function pintarRanking() {
  const hay = estado.estrategias.length > 0;
  $("ranking-vacio").hidden = hay;
  $("tabla-ranking").hidden = !hay;
  if (!hay) return;

  // Comparación: de mayor a menor; en drawdown, de menor a mayor (menos pérdida es mejor).
  const lista = [...estado.estrategias].sort((a, b) => {
    if (ordenActual.campo === "drawdown") {
      return ordenActual.descendente ? a.drawdown - b.drawdown : b.drawdown - a.drawdown;
    }
    return ordenActual.descendente ? b[ordenActual.campo] - a[ordenActual.campo] : a[ordenActual.campo] - b[ordenActual.campo];
  });

  vaciarYAniadir($("cuerpo-ranking"), lista.map((e, i) => {
    const fila = document.createElement("tr");
    fila.dataset.id = e.id;
    if (e.id === estado.seleccionada) fila.classList.add("seleccionada");

    const celdaPos = document.createElement("td");
    celdaPos.textContent = String(i + 1);
    const celdaNombre = document.createElement("td");
    celdaNombre.textContent = e.nombre;
    const celdaPunt = document.createElement("td");
    celdaPunt.className = "num";
    celdaPunt.textContent = fmtNum(e.puntuacion, 1);
    const celdaBen = document.createElement("td");
    celdaBen.className = "num " + (e.beneficio >= 0 ? "positivo" : "negativo");
    celdaBen.textContent = fmtDinero(e.beneficio);
    const celdaDd = document.createElement("td");
    celdaDd.className = "num negativo";
    celdaDd.textContent = fmtDinero(-e.drawdown);
    const celdaAc = document.createElement("td");
    celdaAc.className = "num";
    celdaAc.textContent = fmtNum(e.acierto, 0) + " %";

    vaciarYAniadir(fila, [celdaPos, celdaNombre, celdaPunt, celdaBen, celdaDd, celdaAc]);
    return fila;
  }));
}

// Ordenar al hacer clic en el título de una columna.
document.querySelector("thead").addEventListener("click", (ev) => {
  const th = ev.target.closest("th[data-orden]");
  if (!th) return;
  const campo = th.dataset.orden;
  if (ordenActual.campo === campo) {
    ordenActual.descendente = !ordenActual.descendente;
  } else {
    ordenActual = { campo, descendente: true };
  }
  pintarRanking();
});

// Seleccionar una estrategia al hacer clic en su fila.
$("cuerpo-ranking").addEventListener("click", (ev) => {
  const fila = ev.target.closest("tr[data-id]");
  if (!fila) return;
  estado.seleccionada = fila.dataset.id;
  guardar();
  actualizarTodo();
});

// ── Probador ─────────────────────────────────────────────────
// Gráfica SVG de la curva de capital: línea del capital + línea base del
// capital inicial, con color según acabe en ganancias o pérdidas.
function pintarGraficaCapital(ruta, capitalInicial) {
  const svg = $("grafica-capital");
  const ancho = 600, alto = 220, margen = 8;
  const min = Math.min(...ruta, capitalInicial);
  const max = Math.max(...ruta, capitalInicial);
  const rango = max - min || 1;
  const x = (i) => margen + (i / (ruta.length - 1)) * (ancho - 2 * margen);
  const y = (v) => alto - margen - ((v - min) / rango) * (alto - 2 * margen);
  const puntos = ruta.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const sube = ruta[ruta.length - 1] >= capitalInicial;
  const color = sube ? "var(--verde)" : "var(--rojo)";
  vaciarYAniadir(svg, [
    // línea base del capital inicial
    crearSVG("line", { x1: margen, y1: y(capitalInicial), x2: ancho - margen, y2: y(capitalInicial), stroke: "var(--muted)", "stroke-dasharray": "4 4", "stroke-width": 1 }),
    // curva de capital
    crearSVG("polyline", { points: puntos, fill: "none", stroke: color, "stroke-width": 2 }),
  ]);
}

// Crea un elemento SVG con atributos (los SVG no aceptan setProperty directo).
function crearSVG(etiqueta, atributos) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", etiqueta);
  for (const [k, v] of Object.entries(atributos)) el.setAttribute(k, v);
  return el;
}

// Rellena la pestaña Probador con la estrategia elegida.
function pintarProbador() {
  const e = estado.estrategias.find((x) => x.id === estado.seleccionada);
  $("probador-vacio").hidden = Boolean(e);
  $("probador-contenido").hidden = !e;
  if (!e) return;
  const r = evaluar(e);
  guardar();
  // El campo de capital refleja la preferencia guardada.
  $("campo-capital").value = String(estado.capitalInicial);
  $("prob-nombre").textContent = e.nombre;
  $("prob-entrada").textContent = e.entrada;
  $("prob-salida").textContent = e.salida;
  const ben = $("prob-beneficio");
  ben.textContent = fmtDinero(r.beneficio);
  ben.className = "cuadro-valor " + (r.beneficio >= 0 ? "positivo" : "negativo");
  const dd = $("prob-drawdown");
  dd.textContent = `${fmtDinero(-r.drawdown)} (${fmtNum(r.drawdownPct)} %)`;
  dd.className = "cuadro-valor negativo";
  $("prob-acierto").textContent = fmtNum(r.acierto, 0) + " %";
  $("prob-operaciones").textContent = String(r.operaciones);
  pintarGraficaCapital(r.ruta, estado.capitalInicial);
  pintarRanking();
}

// Al seleccionar una fila, actualizamos también el Probador.
function actualizarTodo() {
  pintarRanking();
  pintarProbador();
  pintarExportar();
}

// ── Optimizador ────────────────────────────────────────────────
// Prueba variaciones de los parámetros de la estrategia elegida
// (stopLoss, takeProfit, velasSalida, periodo) y guarda la mejor.
const OPTIMIZABLES = [
  { clave: "stopLoss", etiqueta: "Stop %", desde: 1, hasta: 15 },
  { clave: "takeProfit", etiqueta: "Objetivo %", desde: 2, hasta: 30 },
  { clave: "velasSalida", etiqueta: "Velas máx.", desde: 5, hasta: 40 },
  { clave: "periodo", etiqueta: "Período indicador", desde: 5, hasta: 30 },
];

function optimizar() {
  const original = estado.estrategias.find((x) => x.id === estado.seleccionada);
  if (!original) return;
  asegurarReglas(original);

  // Variaciones a probar: valores por defecto + algunas alternativas.
  const variantes = [];
  for (const campo of OPTIMIZABLES) {
    const base = original.reglas[campo.clave] ?? original[campo.clave];
    for (const valor of [campo.desde, Math.round((campo.desde + campo.hasta) / 2), campo.hasta]) {
      if (valor !== base) variantes.push({ campo: campo.clave, valor });
    }
  }
  if (variantes.length === 0) return;

  const resultados = variantes.map((v) => {
    const copia = { ...original, reglas: { ...original.reglas, [v.campo]: v.valor } };
    const r = hacerBacktest(copia);
    return { ...v, ...r };
  });
  resultados.sort((a, b) => b.puntuacion - a.puntuacion);
  const mejor = resultados[0];

  // ¿La mejor variación supera a la estrategia original?
  const base = hacerBacktest(original);
  const supera = mejor.puntuacion > base.puntuacion;

  const cont = $("opt-contenido");
  cont.hidden = false;
  $("opt-nombre").textContent = original.nombre;
  const resumen = $("opt-resumen");
  vaciarYAniadir(resumen, [
    crearTexto(`${supera ? "Mejora encontrada" : "La estrategia original ya es la mejor"}: `),
    crearTexto(
      supera
        ? `cambiando ${etiquetaCampo(mejor.campo)} a ${mejor.valor} la puntuación pasa de ${fmtNum(base.puntuacion)} a ${fmtNum(mejor.puntuacion)}.`
        : `ninguna variación de parámetros supera su puntuación actual (${fmtNum(base.puntuacion)}).`
    ),
  ]);

  // Tabla de resultados, mejor variación primero.
  vaciarYAniadir($("opt-tabla-cuerpo"), resultados.map((res) => {
    const fila = document.createElement("tr");
    if (supera && res === mejor) fila.classList.add("seleccionada");
    vaciarYAniadir(fila, [
      crearCelda(etiquetaCampo(res.campo)),
      crearCelda(String(res.valor), "num"),
      crearCelda(fmtNum(res.puntuacion, 1), "num"),
      crearCelda(fmtDinero(res.beneficio), "num " + (res.beneficio >= 0 ? "positivo" : "negativo")),
      crearCelda(fmtDinero(-res.drawdown), "num negativo"),
    ]);
    return fila;
  }));

  // Botón para aplicar la mejor variación a la estrategia original.
  const aplicar = $("boton-aplicar");
  aplicar.disabled = !supera;
  aplicar.onclick = () => {
    original.reglas[mejor.campo] = mejor.valor;
    // Refrescamos los textos de reglas para que coincidan con los parámetros nuevos.
    if (mejor.campo === "periodo") original.entrada = `${textosEntrada[original.reglas.tipo]} (${mejor.valor} períodos)`;
    if (mejor.campo === "velasSalida") original.salida = original.salida.replace(/máx\. \d+ velas/, `máx. ${mejor.valor} velas`);
    if (mejor.campo === "stopLoss") original.salida = original.salida.replace(/Stop −\d+%/g, `Stop −${mejor.valor}%`);
    if (mejor.campo === "takeProfit") original.salida = original.salida.replace(/objetivo \+\d+%/g, `objetivo +${mejor.valor}%`);
    evaluar(original);
    guardar();
    actualizarTodo();
    cont.hidden = true;
    mostrarZona("laboratorio");
  };
}

function etiquetaCampo(clave) {
  const c = OPTIMIZABLES.find((x) => x.clave === clave);
  return c ? c.etiqueta : clave;
}

function crearCelda(texto, clase = "") {
  const td = document.createElement("td");
  td.textContent = texto;
  if (clase) td.className = clase;
  return td;
}

function crearTexto(t) {
  return document.createTextNode(t);
}

// ── Exportar ────────────────────────────────────────────────
// Convierte las reglas de una estrategia a pseudocódigo legible.
function pseudocodigo(e) {
  const r = e.reglas;
  const tipo = ["cruce de medias móviles", "rebote del RSI", "ruptura de rango"][r.tipo];
  return [
    `ESTRATEGIA: ${e.nombre}`,
    `MERCADO: precios diarios simulados (400 velas)`,
    ``,
    `REGLA DE ENTRADA (comprar):`,
    `  ${e.entrada}`,
    ``,
    `REGLA DE SALIDA (vender):`,
    `  ${e.salida}`,
    ``,
    `PARÁMETROS:`,
    `  tipo de regla: ${tipo}`,
    `  período del indicador: ${r.periodo}`,
    `  stop loss: ${r.stopLoss}%`,
    `  take profit: ${r.takeProfit}%`,
    `  máx. velas en posición: ${r.velasSalida}`,
    ``,
    `RESULTADO DEL BACKTEST (datos simulados):`,
    `  puntuación: ${fmtNum(e.puntuacion, 1)}`,
    `  beneficio: ${fmtDinero(e.beneficio)}`,
    `  drawdown: ${fmtDinero(-e.drawdown)}`,
    `  % aciertos: ${fmtNum(e.acierto, 0)}%`,
  ].join("\n");
}

// Rellena la pestaña Exportar con la estrategia elegida.
function pintarExportar() {
  const e = estado.estrategias.find((x) => x.id === estado.seleccionada);
  $("exportar-vacio").hidden = Boolean(e);
  $("exportar-contenido").hidden = !e;
  if (!e) return;
  asegurarReglas(e);
  $("exp-nombre").textContent = e.nombre;
  $("exp-pseudocodigo").textContent = pseudocodigo(e);
}

// Descarga el pseudocódigo como archivo .txt.
function descargarPseudocodigo() {
  const e = estado.estrategias.find((x) => x.id === estado.seleccionada);
  if (!e) return;
  const blob = new Blob([pseudocodigo(e)], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${e.nombre.toLowerCase().replace(/\s+/g, "-")}.txt`;
  a.click();
  URL.revokeObjectURL(url);
}

// Copia el pseudocódigo al portapapeles.
async function copiarPseudocodigo() {
  const e = estado.estrategias.find((x) => x.id === estado.seleccionada);
  if (!e) return;
  try {
    await navigator.clipboard.writeText(pseudocodigo(e));
    const b = $("boton-copiar");
    const textoOriginal = b.textContent;
    b.textContent = "¡Copiado!";
    setTimeout(() => { b.textContent = textoOriginal; }, 1500);
  } catch {
    alert("No se pudo copiar. Selecciona el texto y usa Ctrl+C.");
  }
}

// ── Navegación entre zonas (pestañas) ─────────────────────────────────────
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

// ── Arranque ───────────────────────────────────────────────────────────────
cargar();
serieMercado = crearSerieMercado();
// Las estrategias guardadas con la evaluación provisional se recalculan
// ahora con el backtest real, para que el ranking muestre métricas coherentes.
let recalibrar = false;
for (const e of estado.estrategias) {
  if (!e.evaluada) { evaluar(e); recalibrar = true; }
}
if (recalibrar) guardar();
$("boton-generar").addEventListener("click", generarLote);
$("boton-reprobar").addEventListener("click", () => {
  const valor = Number($("campo-capital").value);
  if (Number.isFinite(valor) && valor >= 100) {
    estado.capitalInicial = valor;
    guardar();
  }
  pintarProbador();
});
$("boton-optimizar").addEventListener("click", optimizar);
$("boton-descargar").addEventListener("click", descargarPseudocodigo);
$("boton-copiar").addEventListener("click", copiarPseudocodigo);
$("num-lote").textContent = String(estado.tamanoLote);
pintarRanking();
mostrarZona("laboratorio");
console.log("TradingLab arrancada. Consola: Ctrl+Mayús+J (Windows).");
