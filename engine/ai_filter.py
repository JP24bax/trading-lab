"""Filtro opcional con IA gratuita: Groq (Llama) o Google AI Studio (Gemini).

La IA NO decide sola: solo puede vetar (o confirmar) una señal que ya salió de una
estrategia validada. Si la IA falla, tarda o llega al límite gratuito, no veta.
No necesita instalar librerías: usa solo la biblioteca estándar de Python.

Si el modelo por defecto no existe en tu cuenta (error 404), consulta la lista real de
modelos disponibles y elige uno automáticamente."""
import json
import os
import re
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))
KEYS_FILE = os.path.join(HERE, "ai_keys.json")

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODELS_URL = "https://api.groq.com/openai/v1/models"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GEMINI_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models?pageSize=200"

PROVIDERS = {
    "groq": dict(label="Groq (gratis)", env="GROQ_API_KEY", model_env="GROQ_MODEL",
                 model="llama-3.3-70b-versatile", url="https://console.groq.com/keys",
                 prefer=["llama-3.3-70b-versatile", "llama-3.1-8b-instant"], any_of="llama"),
    "gemini": dict(label="Google Gemini (AI Studio)", env="GEMINI_API_KEY", model_env="GEMINI_MODEL",
                   model="gemini-1.5-flash", url="https://aistudio.google.com/apikey",
                   prefer=["gemini-1.5-flash", "gemini-2.0-flash"], any_of="flash"),
}
LABELS = {"none": "Sin IA", **{k: v["label"] for k, v in PROVIDERS.items()}}
NOT_CHAT = ("whisper", "guard", "tts", "orpheus", "safeguard", "embed", "image", "audio", "live", "vision")

_MODEL_CACHE = {}


class HttpError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


# ---------- claves ----------
def _read_keys():
    try:
        with open(KEYS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def get_key(provider):
    """Primero la variable de entorno, después ai_keys.json."""
    p = PROVIDERS[provider]
    return (os.getenv(p["env"]) or _read_keys().get(provider) or "").strip()


def save_key(provider, key):
    keys = _read_keys()
    if key:
        keys[provider] = key.strip()
    else:
        keys.pop(provider, None)
    with open(KEYS_FILE, "w", encoding="utf-8") as f:
        json.dump(keys, f)
    try:
        os.chmod(KEYS_FILE, 0o600)   # solo tu usuario puede leer el archivo (en Windows tiene efecto limitado)
    except OSError:
        pass


def model_of(provider):
    """Variable de entorno > modelo detectado automáticamente > modelo por defecto."""
    p = PROVIDERS[provider]
    return os.getenv(p["model_env"]) or _MODEL_CACHE.get(provider) or p["model"]


# ---------- HTTP ----------
def _server_message(raw):
    try:
        d = json.loads(raw)
        e = d.get("error", d)
        return str(e.get("message") if isinstance(e, dict) else e)[:220]
    except Exception:
        return (raw or "")[:220]


def _http_msg(code, detail):
    if code in (401, 403):
        return f"clave no válida o sin permiso (HTTP {code}). {detail}".strip()
    if code == 404:
        return f"modelo o ruta no encontrados (HTTP 404). {detail}".strip()
    if code == 429:
        return f"límite del plan gratuito alcanzado (HTTP 429). {detail}".strip()
    return f"error HTTP {code}. {detail}".strip()


def _request(req, timeout):
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raw = ""
        try:
            raw = e.read().decode()
        except Exception:
            pass
        raise HttpError(e.code, _http_msg(e.code, _server_message(raw)))


def _post(url, headers, body, timeout):
    return _request(urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "iqbot/1.0", **headers}), timeout)


def _get(url, headers, timeout):
    return _request(urllib.request.Request(
        url, method="GET", headers={"User-Agent": "iqbot/1.0", **headers}), timeout)


# ---------- proveedores ----------
def _ask_groq(system, prompt, key, model, timeout):
    hdr = {"Authorization": f"Bearer {key}"}
    body = {"model": model, "temperature": 0, "max_tokens": 800,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
    try:
        data = _post(GROQ_URL, hdr, {**body, "response_format": {"type": "json_object"}}, timeout)
    except HttpError as e:
        if e.code != 400:
            raise
        data = _post(GROQ_URL, hdr, body, timeout)
    text = (data["choices"][0]["message"].get("content") or "").strip()
    if not text:
        raise RuntimeError("el modelo devolvió una respuesta vacía")
    return text


def _ask_gemini(system, prompt, key, model, timeout):
    data = _post(GEMINI_URL.format(model=model), {"x-goog-api-key": key}, {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0, "maxOutputTokens": 512,
                             "responseMimeType": "application/json"},
    }, timeout)
    parts = data["candidates"][0]["content"]["parts"]
    return "".join(p.get("text", "") for p in parts if not p.get("thought"))


ASK = {"groq": _ask_groq, "gemini": _ask_gemini}


def list_models(provider, key, timeout=15):
    """Modelos de chat que tu cuenta puede usar ahora mismo."""
    if provider == "groq":
        data = _get(GROQ_MODELS_URL, {"Authorization": f"Bearer {key}"}, timeout)
        ids = [m["id"] for m in data.get("data", [])]
    else:
        data = _get(GEMINI_MODELS_URL, {"x-goog-api-key": key}, timeout)
        ids = [m["name"].split("/", 1)[1] for m in data.get("models", [])
               if "generateContent" in m.get("supportedGenerationMethods", [])]
    return [i for i in ids if not any(w in i.lower() for w in NOT_CHAT)]


def _pick_model(provider, available, exclude=None):
    p = PROVIDERS[provider]
    for m in p["prefer"]:
        if m in available and m != exclude:
            return m
    for m in available:
        if p["any_of"] in m.lower() and m != exclude and "preview" not in m.lower():
            return m
    return None


def _ask(provider, system, prompt, key, timeout):
    """Pregunta a la IA. Si el modelo no existe (404), busca uno válido en tu cuenta y reintenta."""
    model = model_of(provider)
    try:
        return ASK[provider](system, prompt, key, model, timeout), model
    except HttpError as e:
        if e.code != 404:
            raise
        first = str(e)
    available = list_models(provider, key, timeout)
    alt = _pick_model(provider, available, exclude=model)
    if not alt:
        lst = ", ".join(available[:12]) or "ninguno"
        raise RuntimeError(f"el modelo '{model}' no está disponible y no encontré otro adecuado. "
                           f"Modelos de tu cuenta: {lst}. Detalle: {first}")
    _MODEL_CACHE[provider] = alt
    return ASK[provider](system, prompt, key, alt, timeout), alt


def _parse(text):
    m = re.search(r"\{.*\}", text, re.S)
    d = json.loads(m.group(0) if m else text)
    return bool(d.get("confirm")), str(d.get("reason", ""))[:140]


# ---------- API pública ----------
SYSTEM = ("Eres un filtro de riesgo para operaciones de opciones binarias a corto plazo. Una estrategia "
          "técnica ya validada propone una dirección. Solo puedes confirmarla o vetarla si las velas "
          "recientes la contradicen claramente (por ejemplo, un movimiento fuerte en contra). Si dudas, "
          "confirma. Responde únicamente con JSON.")


def ai_veto(df, direction, asset, provider, strategy="", timeout=6):
    """Devuelve (confirmar: bool, motivo: str). Ante cualquier fallo devuelve True (no veta)."""
    if provider not in PROVIDERS:
        return True, "sin IA"
    key = get_key(provider)
    if not key:
        return True, f"IA desactivada: falta la clave de {PROVIDERS[provider]['label']}"
    try:
        candles = df.tail(20)[["open", "high", "low", "close"]].round(5).values.tolist()
        prompt = (f"Activo {asset}. Estrategia '{strategy}' propone {'CALL (sube)' if direction == 1 else 'PUT (baja)'} "
                  f"a 1 vela de 1 minuto. Últimas 20 velas cerradas [open,high,low,close], la última es la más "
                  f"reciente: {json.dumps(candles)}. Responde en JSON exactamente así: "
                  '{"confirm": true, "reason": "motivo breve"}')
        text, _ = _ask(provider, SYSTEM, prompt, key, timeout)
        ok, why = _parse(text)
        return ok, why or "sin comentario"
    except Exception as e:
        return True, f"IA no disponible ({e}); se sigue sin filtro"


def ai_ping(provider, timeout=15):
    """Prueba la clave y el modelo. Devuelve (ok, mensaje)."""
    if provider not in PROVIDERS:
        return False, "Elige Groq o Gemini."
    key = get_key(provider)
    if not key:
        return False, "Falta la clave API."
    label, default = PROVIDERS[provider]["label"], model_of(provider)
    try:
        text, used = _ask(provider, 'Responde solo con JSON: {"confirm": true, "reason": "ok"}',
                          'Prueba de conexión. Devuelve {"confirm": true, "reason": "ok"}', key, timeout)
        _parse(text)
        extra = f" (el modelo {default} no existía en tu cuenta; se usa {used})" if used != default else ""
        return True, f"Conexión correcta con {label}. Modelo: {used}{extra}."
    except Exception as e:
        msg = f"{label}: {e}"
        if isinstance(e, HttpError) and e.code in (401, 403):
            hint = {"groq": "empiezan por «gsk_» y miden unos 56 caracteres",
                    "gemini": "suelen empezar por «AIza» y medir unos 39 caracteres"}[provider]
            msg += (f" | Tu clave tiene {len(key)} caracteres y empieza por «{key[:4]}». Las de {label.split(' (')[0]} "
                    f"{hint}. Si la tuya es más corta o distinta, bórrala del campo (Ctrl+A, Supr) y pégala "
                    f"de nuevo completa (Ctrl+V).")
        return False, msg