// reglas-ui.js - Pesta\u00f1a "Reglas": editor visual de estrategias din\u00e1micas.
import { guardarRegla, listarEstrategias } from "./motor.js";

const $ = (id) => document.getElementById(id);

async function crearRegla() {
  const nombre = $("regla-nombre").value.trim();
  const indicador = $("regla-indicador").value;
  const condicion = $("regla-condicion").value;
  const valor = parseFloat($("regla-valor").value);
  const panel = $("regla-resultado");

  if (!nombre) { mostrar("Escribe un nombre para la regla.", "error"); return; }
  if (!Number.isFinite(valor)) { mostrar("El valor l\u00edmite debe ser un n\u00famero.", "error"); return; }
  if (/[\s,;()]/.test(nombre)) {
    mostrar("El nombre no puede contener espacios, comas, punto y coma ni par\u00e9ntesis.", "error");
    return;
  }

  panel.hidden = false;
  panel.className = "webhook-resultado";
  panel.textContent = "Registrando la regla en el motor...";

  try {
    const r = await guardarRegla({ nombre, indicador, condicion, valor });
    panel.className = "webhook-resultado exito";
    panel.textContent = r.nombre + " registrada en el motor. " + (r.descripcion || "");
    await refrescarSelectorBacktest();
    limpiarFormulario();
  } catch (e) {
    panel.className = "webhook-resultado error";
    panel.textContent = "No se pudo registrar: " + e.message;
  }
}

function limpiarFormulario() {
  $("regla-nombre").value = "";
  $("regla-valor").value = "";
}

function mostrar(texto, clase) {
  const panel = $("regla-resultado");
  panel.hidden = false;
  panel.textContent = texto;
  panel.className = "webhook-resultado" + (clase ? " " + clase : "");
}

async function refrescarSelectorBacktest() {
  const sel = $("bt-estrategia");
  if (!sel) return;
  try {
    const cat = await listarEstrategias();
    sel.replaceChildren(...cat.estrategias.map((e) => {
      const o = document.createElement("option");
      o.value = e.nombre;
      o.textContent = e.nombre + " - " + e.descripcion.slice(0, 60);
      return o;
    }));
  } catch { /* motor caido: se mantiene el catalogo anterior */ }
}

async function listarCatalogo() {
  const filtro = ($("regla-buscar").value || "").toLowerCase();
  const cont = $("regla-catalogo");
  cont.hidden = false;
  cont.textContent = "Cargando catalogo del motor...";
  try {
    const cat = await listarEstrategias();
    const lista = cat.estrategias.filter((e) => !filtro || e.nombre.toLowerCase().includes(filtro));
    const dinamicas = lista.filter((e) => /custom_|regla_|qa_/i.test(e.nombre));
    const muestra = dinamicas.length ? dinamicas : lista.slice(0, 30);
    if (!muestra.length) { cont.textContent = "Sin resultados para ese filtro."; return; }
    cont.replaceChildren(...muestra.map((e) => {
      const d = document.createElement("div");
      d.className = "regla-item";
      const b = document.createElement("strong");
      b.textContent = e.nombre;
      const p = document.createElement("span");
      p.textContent = " - " + e.descripcion;
      d.append(b, p);
      return d;
    }));
  } catch (e) {
    cont.textContent = "No se pudo leer el catalogo: " + e.message;
  }
}

export function iniciarReglas() {
  if (!$("regla-nombre")) return;
  $("btn-regla-crear").addEventListener("click", crearRegla);
  $("btn-regla-listar").addEventListener("click", listarCatalogo);
  $("regla-buscar").addEventListener("keydown", (e) => { if (e.key === "Enter") listarCatalogo(); });
}
