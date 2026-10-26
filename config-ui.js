// config-ui.js — Pestaña "Config": modo de conexión, credenciales, riesgos, horario y filtro IA.
// Construye el objeto de configuración que consume /session/start (SimpleNamespace del motor).
import { iniciarSesion, aiPing } from "./motor.js";

const $ = (id) => document.getElementById(id);

const CLAVE = "tradinglab.motor.cfg";

// ─ Cargar / guardar la configuración en localStorage ─────────────
function cargarCfg() {
  try {
    return JSON.parse(localStorage.getItem(CLAVE) || "null") || cfgPorDefecto();
  } catch {
    return cfgPorDefecto();
  }
}

function cfgPorDefecto() {
  return {
    sim: true,
    email: "", password: "",
    assets: ["EURUSD"],
    amount: 5000,          // COP por operación
    expiry: 1,             // minutos
    strategy: "auto",
    ai: "none",
    max_loss: 50000,       // COP por sesión
    max_streak: 4,
    max_trades: 30,
    start_hour: 0, end_hour: 24,
    risk_reduction_pct: 50,
  };
}

function guardarCfg(cfg) {
  try { localStorage.setItem(CLAVE, JSON.stringify(cfg)); } catch { }
}

// ─ Form → objeto de configuración (validando números) ─────────────
function leerFormulario() {
  const num = (id, defecto) => {
    const v = parseFloat($(id).value);
    return Number.isFinite(v) ? v : defecto;
  };
  const engine_url = ($("cfg-engine-url").value.trim() || null);
  const activos = $("cfg-activos").value
    .split(",").map((a) => a.trim().toUpperCase()).filter(Boolean);
  const cfg = {
    sim: $("cfg-modo").value === "sim",
    email: $("cfg-email").value.trim() || null,
    password: $("cfg-password").value || null,
    assets: activos.length ? activos : ["EURUSD"],
    amount: num("cfg-amount", 5000),
    expiry: Math.min(15, Math.max(1, Math.round(num("cfg-expiry", 1)))),
    strategy: $("cfg-strategy").value || "auto",
    ai: $("cfg-ai").value,
    max_loss: num("cfg-max-loss", 50000),
    max_streak: Math.round(num("cfg-max-streak", 4)),
    max_trades: Math.round(num("cfg-max-trades", 30)),
    start_hour: Math.min(24, Math.max(0, Math.round(num("cfg-start-hour", 0)))),
    end_hour: Math.min(24, Math.max(0, Math.round(num("cfg-end-hour", 24)))),
    risk_reduction_pct: Math.min(90, Math.max(0, num("cfg-risk-pct", 50))),
    engine_url,
  };
  // Clave IA solo si hay proveedor elegido.
  cfg.ai_key = cfg.ai === "none" ? null : ($("cfg-ai-key").value.trim() || null);
  return cfg;
}

// ─ Objeto → form (al cargar la página) ─────────────
function pintarFormulario(cfg) {
  $("cfg-engine-url").value = cfg.engine_url || "http://localhost:8100";
  $("cfg-modo").value = cfg.sim ? "sim" : "iq";
  $("cfg-email").value = cfg.email || "";
  $("cfg-password").value = cfg.password || "";
  $("cfg-activos").value = (cfg.assets || ["EURUSD"]).join(", ");
  $("cfg-amount").value = cfg.amount;
  $("cfg-expiry").value = cfg.expiry;
  $("cfg-strategy").value = cfg.strategy || "auto";
  $("cfg-ai").value = cfg.ai || "none";
  $("cfg-max-loss").value = cfg.max_loss;
  $("cfg-max-streak").value = cfg.max_streak;
  $("cfg-max-trades").value = cfg.max_trades;
  $("cfg-start-hour").value = cfg.start_hour ?? 0;
  $("cfg-end-hour").value = cfg.end_hour ?? 24;
  $("cfg-risk-pct").value = cfg.risk_reduction_pct ?? 50;
  alternarLogin();
}

function alternarLogin() {
  const esSim = $("cfg-modo").value === "sim";
  $("cfg-login").hidden = esSim;
  $("cfg-aviso-sim").hidden = !esSim;
}

// ─ Guardar e iniciar el bot con esta configuración ─────────────
async function guardarEIniciar() {
  const cfg = leerFormulario();
  const panel = $("cfg-resultado");

  // Validación cruzada de riesgos (como validate_cross_risks de la GUI).
  if (cfg.amount >= cfg.max_loss) {
    mostrar(`⚠️ El importe por operación (${cfg.amount} COP) es mayor o igual a la pérdida máxima (${cfg.max_loss} COP). Ajusta los límites.`, "error");
    return;
  }
  if (!cfg.sim && (!cfg.email || !cfg.password)) {
    mostrar("❌ Modo IQ Option: escribe tu correo y contraseña (o usa Simulación).", "error");
    return;
  }
  if (cfg.ai !== "none" && !cfg.ai_key && !confirm(`Filtro IA elegido (${cfg.ai}) sin clave API: el filtro quedará desactivado si no hay clave guardada en el motor. ¿Continuar?`)) {
    return;
  }

  guardarCfg(cfg);
  // motor.js lee engine_url de esta clave: sincronízala.
  try {
    const prev = JSON.parse(localStorage.getItem("tradinglab.motor.cfg") || "null") || {};
    localStorage.setItem("tradinglab.motor.cfg", JSON.stringify({ ...prev, engine_url: cfg.engine_url || "http://localhost:8100" }));
  } catch { }
  panel.hidden = false;
  panel.className = "webhook-resultado";
  panel.textContent = "⏳ Enviando configuración al motor e iniciando sesión...";

  try {
    // La clave IA se guarda en el motor si el usuario la trajo.
    if (cfg.ai !== "none" && cfg.ai_key) {
      await aiPing(cfg.ai, cfg.ai_key);   // valida y persiste la clave en el motor
    }
    await iniciarSesion(cfg);
    panel.className = "webhook-resultado exito";
    panel.textContent = `✅ Sesión iniciada en modo ${cfg.sim ? "SIMULACIÓN" : "IQ Option DEMO"}. ` +
      `Activo(s): ${cfg.assets.join(", ")} · ${cfg.amount} COP · expiración ${cfg.expiry} min · ` +
      `máx. ${cfg.max_trades} ops · pérdida máx. ${cfg.max_loss} COP. Mira el progreso en la pestaña "Motor Bot".`;
  } catch (e) {
    panel.className = "webhook-resultado error";
    panel.textContent = `❌ No se pudo iniciar: ${e.message}`;
  }
}

function mostrar(texto, clase) {
  const panel = $("cfg-resultado");
  panel.hidden = false;
  panel.textContent = texto;
  panel.className = "webhook-resultado" + (clase ? " " + clase : "");
}

// ─ Probar la conexión del filtro IA ─────────────
async function probarIA() {
  const proveedor = $("cfg-ai").value;
  if (proveedor === "none") { mostrar("Elige primero Groq o Gemini en el desplegable.", "error"); return; }
  const clave = $("cfg-ai-key").value.trim() || null;
  mostrar(`⏳ Probando conexión con ${proveedor}...`, "");
  try {
    const r = await aiPing(proveedor, clave);
    mostrar(`${r.ok ? "✅" : "❌"} ${r.msg}`, r.ok ? "exito" : "error");
  } catch (e) {
    mostrar(`❌ Error contactando el motor: ${e.message}`, "error");
  }
}

export function iniciarConfig() {
  if (!$("cfg-modo")) return;
  pintarFormulario(cargarCfg());
  $("cfg-modo").addEventListener("change", alternarLogin);
  $("cfg-ai").addEventListener("change", () => { $("cfg-ai-key-row").hidden = $("cfg-ai").value === "none"; });
  $("cfg-ai-key-row").hidden = ($("cfg-ai").value === "none");
  $("btn-cfg-iniciar").addEventListener("click", guardarEIniciar);
  $("btn-cfg-guardar").addEventListener("click", () => {
    const cfg = leerFormulario();
    guardarCfg(cfg);
    mostrar("Configuración guardada (no se ha iniciado el bot).", "exito");
  });
  $("btn-cfg-probar-ia").addEventListener("click", probarIA);
}
