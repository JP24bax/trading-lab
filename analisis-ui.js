// analisis-ui.js — Tarjeta "Evidencia y condiciones" (pestaña Motor Bot).
// 1) Puerta de la demo: ¿hay evidencia (100-200 ops, una estrategia, vs equilibrio) para pensar en dinero real?
// 2) ¿En qué condiciones se gana o se pierde? (hora, activo, estrategia, volatilidad, tendencia).
// Los grupos con menos de 100 operaciones se muestran atenuados: son ruido, no conclusiones.
import { analisisOperaciones, veredictoDemo } from "./motor.js";

const $ = (id) => document.getElementById(id);

const TITULOS = {
  hora: "Por hora (Colombia)",
  activo: "Por activo",
  estrategia: "Por estrategia",
  direccion: "CALL vs PUT",
  volatilidad: "Por volatilidad (ATR vs normal)",
  tendencia: "Respecto a la tendencia de 15 min",
};

const pct = (v, d = 1) => (v == null ? "—" : (v * 100).toLocaleString("es-CO", { maximumFractionDigits: d }) + "%");

function celda(fila, texto, clase = "") {
  const c = document.createElement("td");
  c.textContent = texto;
  if (clase) c.className = clase;
  fila.appendChild(c);
}

function tablaDimension(filas) {
  const t = document.createElement("table");
  const cab = document.createElement("thead");
  const trc = document.createElement("tr");
  for (const [txt, cls] of [["Grupo", ""], ["Ops", "num"], ["Win rate", "num"], ["Equilibrio", "num"], ["EV/op", "num"], ["Lectura", ""]]) {
    const th = document.createElement("th");
    th.textContent = txt;
    if (cls) th.className = cls;
    trc.appendChild(th);
  }
  cab.appendChild(trc);
  const cuerpo = document.createElement("tbody");
  for (const f of filas) {
    const tr = document.createElement("tr");
    if (!f.suficiente) tr.className = "fila-ruido";
    celda(tr, f.grupo);
    celda(tr, String(f.n), "num");
    celda(tr, pct(f.winrate), "num");
    celda(tr, pct(f.equilibrio), "num");
    celda(tr, (f.ev ?? 0).toFixed(3), "num");
    celda(tr, f.lectura);
    cuerpo.appendChild(tr);
  }
  t.append(cab, cuerpo);
  return t;
}

async function actualizar() {
  const boton = $("btn-analisis");
  boton.disabled = true;
  try {
    const [demo, an] = await Promise.all([veredictoDemo(), analisisOperaciones()]);
    const v = $("demo-veredicto");
    v.hidden = false;
    v.textContent = demo.texto;
    v.className = "bt-veredicto " + (demo.nivel === "good" ? "exito" : demo.nivel === "bad" ? "error" : "warn");
    const partes = [`Operaciones demo contadas: ${demo.contadas ?? 0}`];
    if (demo.excluidas_sim) partes.push(`excluidas (simuladas o de origen desconocido): ${demo.excluidas_sim}`);
    if (demo.n >= 1 && demo.winrate != null) {
      partes.push(`win rate ${pct(demo.winrate)} · equilibrio ${pct(demo.equilibrio)} · IC95 ${pct(demo.ic95[0])}-${pct(demo.ic95[1])}`);
    }
    $("demo-detalle").textContent = partes.join(" · ");

    const cont = $("analisis-dims");
    cont.replaceChildren();
    if (!an.contadas) {
      const p = document.createElement("p");
      p.className = "vacio";
      p.textContent = "Cuando haya operaciones en cuenta demo aparecerá aquí el desglose por condiciones.";
      cont.appendChild(p);
      return;
    }
    for (const [dim, filas] of Object.entries(an.dimensiones)) {
      if (!filas.length) continue;
      const det = document.createElement("details");
      det.style.marginTop = "8px";
      const sum = document.createElement("summary");
      sum.style.cursor = "pointer";
      sum.textContent = TITULOS[dim] || dim;
      det.append(sum, tablaDimension(filas));
      cont.appendChild(det);
    }
  } catch (e) {
    $("demo-veredicto").hidden = false;
    $("demo-veredicto").className = "bt-veredicto error";
    $("demo-veredicto").textContent = `No se pudo consultar el motor: ${e.message}`;
  } finally {
    boton.disabled = false;
  }
}

export function iniciarAnalisis() {
  if (!$("btn-analisis")) return;
  $("btn-analisis").addEventListener("click", actualizar);
}
