// Pruebas del núcleo del laboratorio (motor-binario.js). Ejecutar:  node test-motor-binario.mjs
// 1) MACD: cada vela devuelve SU valor (antes la caché devolvía siempre el de la primera vela consultada).
// 2) Una estrategia MACD genera operaciones (antes: 0).
// 3) El .py que exporta la web da la misma señal que el evaluador JS (necesita python3 + pandas + pandas_ta).
import { macd, ema, rng, generarEstrategia, aPython, senal, serieDesdeCierres, backtest } from "./motor-binario.js";
import { writeFileSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";

let fallos = 0;
const ok = (cond, msg) => { console.log(`  ${cond ? "OK   " : "FALLA"} ${msg}`); if (!cond) fallos++; };

function serie(n, semilla) {
  const r = rng(semilla), cierres = [];
  let p = 1.1;
  for (let i = 0; i < n; i++) {
    const u1 = Math.max(r(), 1e-12), u2 = r();
    p *= Math.exp(0.0004 * Math.sqrt(-2 * Math.log(u1)) * Math.cos(2 * Math.PI * u2));
    cierres.push(p);
  }
  return cierres;
}

// ── 1) MACD frente a una referencia calculada desde cero ──
{
  const c = serie(600, 7);
  const ref = (i) => {
    const k = 2 / 10; let s = null, h = null;
    for (let j = 35; j <= i; j++) {
      const l = ema(c, 12, j) - ema(c, 26, j);
      s = s === null ? l : l * k + s * (1 - k);
      h = l - s;
    }
    return h;
  };
  const cNueva = [...c];                       // serie nueva: caché limpia
  const orden = [400, 100, 250, 399, 36, 599, 300];
  const malas = orden.filter((i) => Math.abs(macd(cNueva, i).hist - ref(i)) > 1e-12);
  ok(malas.length === 0, `MACD correcto en cualquier orden de consulta (fallan: ${malas.join(",") || "ninguna"})`);
  ok(macd(cNueva, 34) === null && macd(cNueva, 35) !== null, "MACD respeta el arranque (índice 35)");
}

// ── 2) Una estrategia MACD opera ──
{
  const velas = serieDesdeCierres(serie(3000, 11));
  const e = { entrada_call: [{ indicador: "MACD", operador: "cruce_alcista" }],
              entrada_put: [{ indicador: "MACD", operador: "cruce_bajista" }],
              expiracion: 1, gestion: { monto: 10 } };
  const r = backtest(e, velas, 0.85);
  ok(r.operaciones > 100, `la estrategia MACD genera operaciones (${r.operaciones})`);
}

// ── 3) Paridad del exportador Python ──
{
  const cierres = serie(1500, 21);
  const velas = serieDesdeCierres(cierres), cierra = velas.map((v) => v.cierre);
  const casos = [];
  const usados = new Set();
  for (let semilla = 1; casos.length < 60 && semilla < 400; semilla++) {
    const e = generarEstrategia(semilla);
    const clave = JSON.stringify([e.entrada_call, e.entrada_put]);
    if (usados.has(clave)) continue;
    usados.add(clave);
    const posiciones = [];
    let sinSenal = 0;
    for (let k = 400; k < 1500; k++) {
      const s = senal(e, velas, cierra, k);
      if (s) posiciones.push([k, s]);
      else if (sinSenal < 25 && k % 37 === 0) { posiciones.push([k, null]); sinSenal++; }
    }
    casos.push({ semilla, codigo: aPython(e, 0.85), posiciones });
  }
  const dir = mkdtempSync(join(tmpdir(), "paridad-"));
  const f = join(dir, "casos.json");
  writeFileSync(f, JSON.stringify({ cierres, casos }));
  const r = spawnSync("python3", ["test-paridad-exportador.py", f], { encoding: "utf-8" });
  let res = null;
  try { res = JSON.parse(r.stdout.trim().split("\n").pop()); } catch { /* sin salida válida */ }
  if (!res) {
    ok(false, `el harness de Python falló: ${(r.stderr || r.stdout).slice(0, 300)}`);
  } else if (res.saltado) {
    console.log(`  SALTADO paridad del exportador: ${res.saltado}`);
  } else {
    ok(res.errores.length === 0, `los .py exportados cargan y se ejecutan sin error ${res.errores[0] || ""}`);
    const acuerdo = res.coinciden / res.total;
    ok(acuerdo >= 0.98, `misma señal que el evaluador JS en ${(acuerdo * 100).toFixed(1)}% de ${res.total} velas (mínimo 98%)`);
    const rec = res.con_senal_js ? res.recuperadas / res.con_senal_js : 1;
    ok(res.con_senal_js > 30 && rec >= 0.9, `reproduce ${(rec * 100).toFixed(1)}% de las ${res.con_senal_js} señales del JS (mínimo 90%)`);
  }
}

console.log(fallos ? `\n${fallos} comprobaciones fallan` : "\nTodo correcto");
process.exit(fallos ? 1 : 0);
