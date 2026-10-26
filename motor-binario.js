// motor-binario.js — Núcleo cuantitativo para opciones binarias/digitales (IQ Option).
// Genera estrategias, las evalúa con payoff fijo (ganancia = monto×payout, pérdida = monto)
// resolviendo el resultado exactamente N velas tras la señal, aplica filtro genético
// por Win Rate y exporta a JSON / Python. Sin librerías externas.

// ── Generador determinista (xorshift32) ─────────────────────────────────
export function rng(seed) {
  let x = (seed >>> 0) || 1;
  return () => {
    x ^= x << 13; x ^= x >>> 17; x ^= x << 5;
    return (x >>> 0) / 4294967296;
  };
}

// ── Indicadores técnicos ────────────────────────────────────────────────
const cacheSma = new WeakMap();
export function sma(cierra, n, i) {
  if (i < n - 1) return null;
  let cache = cacheSma.get(cierra);
  if (!cache) { cache = new Map(); cacheSma.set(cierra, cache); }
  const clave = `${n}:${i}`;
  const hit = cache.get(clave);
  if (hit !== undefined) return hit;
  let s = 0;
  for (let j = i - n + 1; j <= i; j++) s += cierra[j];
  const valor = s / n;
  cache.set(clave, valor);
  return valor;
}

const cacheEma = new WeakMap();
export function ema(cierra, n, i) {
  if (i < n - 1) return null;
  // Memoización por (serie, periodo): cada valor se calcula una sola vez → O(1) amortizado.
  let cache = cacheEma.get(cierra);
  if (!cache) { cache = new Map(); cacheEma.set(cierra, cache); }
  const clave = `${n}:${i}`;
  if (cache.has(clave)) return cache.get(clave);
  const k = 2 / (n + 1);
  let e = 0;
  for (let j = 0; j < n; j++) e += cierra[j];
  e /= n;
  cache.set(`${n}:${n - 1}`, e);
  for (let j = n; j <= i; j++) {
    const prev = cache.get(`${n}:${j - 1}`);
    e = cierra[j] * k + prev * (1 - k);
    cache.set(`${n}:${j}`, e);
  }
  return e;
}

const cacheRsi = new WeakMap();
export function rsi(cierra, n, i) {
  if (i < n) return null;
  let cache = cacheRsi.get(cierra);
  if (!cache) { cache = new Map(); cacheRsi.set(cierra, cache); }
  const clave = `${n}:${i}`;
  const hit = cache.get(clave);
  if (hit !== undefined) return hit;
  let sub = 0, baj = 0;
  for (let j = i - n + 1; j <= i; j++) {
    const d = cierra[j] - cierra[j - 1];
    if (d > 0) sub += d; else baj -= d;
  }
  const valor = sub + baj === 0 ? 50 : 100 - 100 / (1 + sub / baj);
  cache.set(clave, valor);
  return valor;
}

const cacheBoll = new WeakMap();
export function bandasBollinger(cierra, n, i, mult = 2) {
  if (i < n - 1) return null;
  let cache = cacheBoll.get(cierra);
  if (!cache) { cache = new Map(); cacheBoll.set(cierra, cache); }
  const clave = `${n}:${mult}:${i}`;
  const hit = cache.get(clave);
  if (hit !== undefined) return hit;
  const m = sma(cierra, n, i);
  if (m === null) return null;
  let v = 0;
  for (let j = i - n + 1; j <= i; j++) v += (cierra[j] - m) ** 2;
  const sd = Math.sqrt(v / n);
  const valor = { sup: m + mult * sd, inf: m - mult * sd, media: m };
  cache.set(clave, valor);
  return valor;
}

const cacheMacd = new WeakMap();
export function macd(cierra, i, rapida = 12, lenta = 26, senal = 9) {
  if (i < lenta + senal) return null;
  const k = 2 / (senal + 1);
  let cache = cacheMacd.get(cierra);
  if (!cache) { cache = new Map(); cacheMacd.set(cierra, cache); }
  const clave = `${rapida}:${lenta}:${senal}`;
  if (cache.has(clave)) return cache.get(clave);
  // Se calcula toda la serie hasta i de una pasada, cacheando cada vela.
  let s = null, linea = null;
  for (let j = lenta + senal; j <= i; j++) {
    linea = ema(cierra, rapida, j) - ema(cierra, lenta, j);
    s = s === null ? linea : linea * k + s * (1 - k);
    cache.set(clave, { linea, senal: s, hist: linea - s, idx: j });
    // Guardamos por índice para no pisar resultados de llamadas con i menor.
    cache.set(`${clave}@${j}`, cache.get(clave));
  }
  const ultimo = cache.get(`${clave}@${i}`);
  cache.set(clave, ultimo); // la entrada por clave queda en el índice más lejano
  return ultimo;
}

const cacheStoch = new WeakMap();
export function estocastico(velas, i, n = 14) {
  if (i < n) return null;
  let cache = cacheStoch.get(velas);
  if (!cache) { cache = new Map(); cacheStoch.set(velas, cache); }
  const clave = `${n}:${i}`;
  const hit = cache.get(clave);
  if (hit !== undefined) return hit;
  let max = -Infinity, min = Infinity;
  for (let j = i - n + 1; j <= i; j++) {
    max = Math.max(max, velas[j].maximo);
    min = Math.min(min, velas[j].minimo);
  }
  const valor = max === min ? 50 : ((velas[i].cierre - min) / (max - min)) * 100;
  cache.set(clave, valor);
  return valor;
}

// ── Esquema de estrategia (JSON evaluable) ──────────────────────────────
// {
//   activo, timeframe, expiracion,       → "EURUSD", "1m"|"5m", 1|5|15 (min)
//   gestion: { monto, tipo_operacion, payout_minimo },
//   entrada_call:  [{ indicador, operador, periodo?, umbral? }, ...],  (todas deben cumplirse)
//   entrada_put:   [{ ... }, ...]
// }
const INDICADORES = ["RSI", "EMA", "SMA", "BOLLINGER", "MACD", "STOCH"];
const OPERADORES_CRUZ = ["cruce_alcista", "cruce_bajista"];
const OPERADORES_UMBRAL = ["menor_que", "mayor_que"];
const TIMEFRAMES = ["1m", "5m"];
const EXPIRACIONES = [1, 5, 15];
const ACTIVOS = ["EURUSD", "GBPUSD", "USDJPY", "AUDCAD", "EURJPY"];

// Condiciones de entrada generables (se emparejan: la versión CALL y su PUT espejo).
const PLANTILLAS = [
  { indicador: "RSI", periodo: [7, 21], call: { indicador: "RSI", operador: "menor_que", umbral: 30 }, put: { indicador: "RSI", operador: "mayor_que", umbral: 70 } },
  { indicador: "STOCH", periodo: [9, 14], call: { indicador: "STOCH", operador: "menor_que", umbral: 20 }, put: { indicador: "STOCH", operador: "mayor_que", umbral: 80 } },
  { indicador: "BOLLINGER", periodo: [20, 20], call: { indicador: "BOLLINGER", operador: "cruce_alcista", banda: "inf" }, put: { indicador: "BOLLINGER", operador: "cruce_bajista", banda: "sup" } },
  { indicador: "EMA", periodo: [5, 50], call: { indicador: "EMA", operador: "cruce_alcista", periodo_rapida: 9 }, put: { indicador: "EMA", operador: "cruce_bajista", periodo_rapida: 9 } },
  { indicador: "MACD", periodo: [0, 0], call: { indicador: "MACD", operador: "cruce_alcista" }, put: { indicador: "MACD", operador: "cruce_bajista" } },
  { indicador: "SMA", periodo: [10, 50], call: { indicador: "SMA", operador: "cruce_alcista", periodo_rapida: 10 }, put: { indicador: "SMA", operador: "cruce_bajista", periodo_rapida: 10 } },
];

function aleatorio(rngFn, lista) { return lista[Math.floor(rngFn() * lista.length)]; }
function rango(rngFn, [a, b]) { return a + Math.floor(rngFn() * (b - a + 1)); }

// Genera una estrategia binaria completa a partir de una semilla.
export function generarEstrategia(seed) {
  const r = rng(seed * 977 + 13);
  const plantilla = aleatorio(r, PLANTILLAS);
  const periodo = plantilla.periodo[0] === plantilla.periodo[1]
    ? plantilla.periodo[0]
    : rango(r, plantilla.periodo);
  const nombre = `${plantilla.indicador} ${periodo} ${aleatorio(r, ["A", "B", "C", "D", "E"])}${1 + Math.floor(r() * 9)}`;

  const limpiar = (c) => {
    const copia = { ...c };
    if (c.periodo_rapida) copia.periodo_rapida = Math.min(periodo, c.periodo_rapida);
    if (plantilla.periodo[0] > 0) copia.periodo = periodo;
    return copia;
  };

  return {
    id: crypto.randomUUID(),
    nombre,
    semilla: seed,
    activo: aleatorio(r, ACTIVOS),
    timeframe: aleatorio(r, TIMEFRAMES),
    expiracion: aleatorio(r, EXPIRACIONES),
    gestion: { monto: 10, tipo_operacion: r() < 0.5 ? "BINARY" : "DIGITAL", payout_minimo: 0.75 },
    entrada_call: [limpiar(plantilla.call)],
    entrada_put: [limpiar(plantilla.put)],
    // métricas (las llena evaluar):
    evaluada: false, operaciones: 0, acierto: 0, beneficio: 0, puntuacion: 0,
  };
}

// ── Evaluación de una condición sobre la vela i ─────────────────────────
function cumple(cond, velas, cierra, i, lado) {
  const p = cond.periodo ?? cond.periodo_rapida ?? 14;
  switch (cond.indicador) {
    case "RSI": {
      const v = rsi(cierra, p, i);
      if (v === null) return false;
      return cond.operador === "menor_que" ? v < cond.umbral : v > cond.umbral;
    }
    case "STOCH": {
      const v = estocastico(velas, i, p);
      if (v === null) return false;
      return cond.operador === "menor_que" ? v < cond.umbral : v > cond.umbral;
    }
    case "BOLLINGER": {
      const b = bandasBollinger(cierra, 20, i);
      const bAnt = bandasBollinger(cierra, 20, i - 1);
      if (!b || !bAnt) return false;
      const banda = cond.banda === "sup" ? b.sup : b.inf;
      const bandaAnt = cond.banda === "sup" ? bAnt.sup : bAnt.inf;
      if (cond.operador === "cruce_alcista") return cierra[i - 1] < bandaAnt && cierra[i] > banda;
      return cierra[i - 1] > bandaAnt && cierra[i] < banda;
    }
    case "EMA": case "SMA": {
      const fn = cond.indicador === "EMA" ? ema : sma;
      const lenta = fn(cierra, p, i), rapida = fn(cierra, cond.periodo_rapida ?? 9, i);
      const lentaAnt = fn(cierra, p, i - 1), rapidaAnt = fn(cierra, cond.periodo_rapida ?? 9, i - 1);
      if ([lenta, rapida, lentaAnt, rapidaAnt].some((v) => v === null)) return false;
      if (cond.operador === "cruce_alcista") return rapidaAnt <= lentaAnt && rapida > lenta;
      return rapidaAnt >= lentaAnt && rapida < lenta;
    }
    case "MACD": {
      const m = macd(cierra, i), mAnt = macd(cierra, i - 1);
      if (!m || !mAnt) return false;
      if (cond.operador === "cruce_alcista") return mAnt.hist <= 0 && m.hist > 0;
      return mAnt.hist >= 0 && m.hist < 0;
    }
    default:
      return false;
  }
}

function senal(e, velas, cierra, i) {
  if (e.entrada_call.every((c) => cumple(c, velas, cierra, i))) return "CALL";
  if (e.entrada_put.every((c) => cumple(c, velas, cierra, i))) return "PUT";
  return null;
}

// ── Backtest binario: payoff fijo, resolución N velas después ───────────
// ganancia = monto × payout · pérdida = monto · resultado a cierre de vela i+expiracion
export function backtest(e, velas, opciones = 0.8, montoOverride = null) {
  // Acepta {payout, monto} como objeto o un número plano de payout.
  const esObj = opciones !== null && typeof opciones === 'object';
  const payout = esObj ? (opciones.payout ?? 0.8) : opciones;
  const cierra = velas.map((v) => v.cierre);
  const monto = montoOverride ?? (esObj && opciones.monto) ?? e.gestion.monto;
  const gap = Math.max(1, Math.round(e.expiracion)); // velas hasta la resolución
  let ops = 0, ganadas = 0, pnl = 0;
  let maxPnl = 0, drawdown = 0; // en unidades monetarias

  for (let i = 1; i < velas.length - gap; i++) {
    const s = senal(e, velas, cierra, i);
    if (!s) continue;
    const entrada = cierra[i];
    const resolucion = cierra[i + gap];
    const gana = s === "CALL" ? resolucion > entrada : resolucion < entrada;
    // Empate (mismo precio): se devuelve el monto (reembolso, PnL 0).
    ops++;
    if (resolucion === entrada) { /* reembolso */ }
    else if (gana) { pnl += monto * payout; ganadas++; }
    else { pnl -= monto; }
    if (pnl > maxPnl) maxPnl = pnl;
    const caida = maxPnl - pnl;
    if (caida > drawdown) drawdown = caida;
  }

  const acierto = ops ? (ganadas / ops) * 100 : 0;
  return {
    operaciones: ops,
    ganadas,
    acierto: Math.round(acierto * 10) / 10,
    beneficio: Math.round(pnl * 100) / 100,
    drawdown: Math.round(drawdown * 100) / 100,
    // Puntuación genética: prioriza Win Rate, luego beneficio.
    puntuacion: Math.round((acierto * 10 + pnl) * 10) / 10,
    winRateOk: acierto >= 58,
  };
}

// Serializa a serie de velas un histórico de precios (solo cierres + rangos).
export function serieDesdeCierres(cierres) {
  return cierres.map((c, i) => ({
    cierre: c,
    maximo: Math.max(c, cierres[i - 1] ?? c),
    minimo: Math.min(c, cierres[i - 1] ?? c),
  }));
}

// ── Mutación genética ───────────────────────────────────────────────────
export function mutar(e, seed) {
  const r = rng(seed);
  const hijo = JSON.parse(JSON.stringify(e));
  hijo.id = crypto.randomUUID();
  hijo.semilla = seed;
  hijo.nombre = e.nombre + "′" + Math.floor(r() * 90 + 10);
  const opciones = ["expiracion", "gestion", "condiciones"];
  const elegir = aleatorio(r, opciones);
  if (elegir === "expiracion") hijo.expiracion = aleatorio(r, EXPIRACIONES);
  else if (elegir === "gestion") hijo.gestion.monto = [5, 10, 20, 50][Math.floor(r() * 4)];
  else {
    const plantilla = aleatorio(r, PLANTILLAS);
    const periodo = plantilla.periodo[0] === plantilla.periodo[1] ? plantilla.periodo[0] : rango(r, plantilla.periodo);
    const limpiar = (c) => {
      const copia = { ...c };
      if (c.periodo_rapida) copia.periodo_rapida = Math.min(periodo, c.periodo_rapida);
      return copia;
    };
    hijo.entrada_call = [limpiar(plantilla.call)];
    hijo.entrada_put = [limpiar(plantilla.put)];
  }
  return hijo;
}

// ── Motor de exportación: JSON evaluable ────────────────────────────────
export function aJSON(e) {
  const { id, nombre, activo, timeframe, expiracion, gestion, entrada_call, entrada_put } = e;
  return JSON.stringify({
    schema: "tradinglab.strategy/1.0",
    id, nombre, activo, timeframe,
    expiracion_minutos: expiracion,
    gestion,
    entrada_call, entrada_put,
  }, null, 2);
}

// ── Motor de exportación: script Python autónomo ────────────────────────
// Compila la lógica condicional en un .py que usa pandas_ta sobre datos de IQ Option.
export function aPython(e, payout = 0.8) {
  const condPy = (c, v = "df") => {
    const p = c.periodo_rapida ?? c.periodo ?? 14;
    switch (c.indicador) {
      case "RSI": return `(df["rsi_${p}"] ${c.operador === "menor_que" ? "<" : ">"} ${c.umbral})`;
      case "STOCH": return `(df["stoch_${p}"] ${c.operador === "menor_que" ? "<" : ">"} ${c.umbral})`;
      case "BOLLINGER": {
        const banda = c.banda === "sup" ? "bbl_20_2" : "bbu_20_2";
        return c.operador === "cruce_alcista"
          ? `((df["close"].shift(1) < df["${banda}"].shift(1)) & (df["close"] > df["${banda}"]))`
          : `((df["close"].shift(1) > df["${banda}"].shift(1)) & (df["close"] < df["${banda}"]))`;
      }
      case "EMA": return c.operador === "cruce_alcista"
        ? `((df["ema_${c.periodo_rapida}"].shift(1) <= df["ema_${p}"].shift(1)) & (df["ema_${c.periodo_rapida}"] > df["ema_${p}"]))`
        : `((df["ema_${c.periodo_rapida}"].shift(1) >= df["ema_${p}"].shift(1)) & (df["ema_${c.periodo_rapida}"] < df["ema_${p}"]))`;
      case "SMA": return c.operador === "cruce_alcista"
        ? `((df["sma_${c.periodo_rapida}"].shift(1) <= df["sma_${p}"].shift(1)) & (df["sma_${c.periodo_rapida}"] > df["sma_${p}"]))`
        : `((df["sma_${c.periodo_rapida}"].shift(1) >= df["sma_${p}"].shift(1)) & (df["sma_${c.periodo_rapida}"] < df["sma_${p}"]))`;
      case "MACD": return c.operador === "cruce_alcista"
        ? `((df["macdh_12_26_9"].shift(1) <= 0) & (df["macdh_12_26_9"] > 0))`
        : `((df["macdh_12_26_9"].shift(1) >= 0) & (df["macdh_12_26_9"] < 0))`;
      default: return "False";
    }
  };
  const call = e.entrada_call.map((c) => condPy(c)).join(" & ");
  const put = e.entrada_put.map((c) => condPy(c)).join(" & ");

  return `# ${e.nombre} — generado por TradingLab
# Estrategia binaria para IQ Option · PAYOUT = ${payout}
# Requiere: pip install pandas pandas-ta iqoptionapi  (o iqoptionapi-stable-api)
import time
import pandas as pd
import pandas_ta as ta                     # indicadores: RSI, EMA, SMA, BB, MACD, STOCH
from iqoptionapi.stable_api import IQ_Option

EMAIL = "tu_correo@ejemplo.com"            # ← configura tus credenciales (usa cuenta PRACTICE)
PASSWORD = "tu_contraseña"
ACTIVO = "${e.activo}"
MONTO = ${e.gestion.monto}
EXPIRACION_MIN = ${e.expiracion}
TIPO = "${e.gestion.tipo_operacion}"
PAYOUT_MINIMO = ${e.gestion.payout_minimo}
PAYOUT = ${payout}

def obtener_velas(iq, activo=ACTIVO, velas=200, timeframe=60):
    iq.start_candles_stream(activo, timeframe, velas)
    time.sleep(2)
    datos = iq.get_realtime_candles(activo, timeframe)
    iq.stop_candles_stream(activo)
    df = pd.DataFrame(datos).T
    df = df.rename(columns={"max": "high", "min": "low"})
    df["close"] = df["close"].astype(float)
    df["rsi_14"] = ta.rsi(df["close"], 14)
    df["ema_9"], df["ema_21"], df["ema_50"] = ta.ema(df["close"], 9), ta.ema(df["close"], 21), ta.ema(df["close"], 50)
    df["sma_10"], df["sma_50"] = ta.sma(df["close"], 10), ta.sma(df["close"], 50)
    bb = ta.bbands(df["close"], 20)
    df["bbl_20_2"], df["bbu_20_2"] = bb["BBL_20_2.0"], bb["BBU_20_2.0"]
    macd = ta.macd(df["close"])
    df["macdh_12_26_9"] = macd["MACDh_12_26_9"]
    df["stoch_14"] = ta.stoch(df["high"], df["low"], df["close"], 14)["STOCHk_14_3_3"]
    return df

def hay_senal(df):
    ultima = df.iloc[-1]
    call = ${call}
    put = ${put}
    if call: return "CALL"
    if put: return "PUT"
    return None

def operar(iq, direccion):
    profit = iq.get_all_profit().get(ACTIVO, {})
    payout_actual = profit.get("binary" if TIPO == "BINARY" else "digital", {}).get("profit", 0)
    if payout_actual < PAYOUT_MINIMO:
        print(f"[SKIP] payout {payout_actual:.0%} < {PAYOUT_MINIMO:.0%}")
        return
    if TIPO == "BINARY":
        resultado = iq.buy(MONTO, ACTIVO, direccion, EXPIRACION_MIN)
    else:
        resultado = iq.buy_digital_spot_v2(ACTIVO, MONTO, direccion, EXPIRACION_MIN)
    print(f"[ORDEN] {direccion} {ACTIVO} {MONTO}€ exp={EXPIRACION_MIN}m → {resultado}")

def main():
    iq = IQ_Option(EMAIL, PASSWORD)
    if not iq.connect():
        raise SystemExit("Login fallido")
    iq.change_balance("PRACTICE")
    print("Bot ${e.nombre} en marcha (PRACTICE). Ctrl+C para salir.")
    while True:
        df = obtener_velas(iq)
        s = hay_senal(df)
        if s:
            print(f"[SENAL] {s} @ {df['close'].iloc[-1]}")
            operar(iq, s)
            time.sleep(EXPIRACION_MIN * 60)  # espera la resolución antes de reevaluar
        time.sleep(5)

if __name__ == "__main__":
    main()
`;
}

// ── Webhook dispatcher (Parte 1: envía la señal al middleware) ──────────
export async function enviarSenal(url, token, e, accion, montoOverride = null) {
  const payload = {
    secret_token: token,
    par: e.activo,
    accion,
    monto: montoOverride ?? e.gestion.monto,
    expiracion_minutos: e.expiracion,
    tipo_operacion: e.gestion.tipo_operacion,
    payout_minimo: e.gestion.payout_minimo,
  };
  const respuesta = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return respuesta.json();
}
