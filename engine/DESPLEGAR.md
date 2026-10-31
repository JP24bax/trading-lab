# TradingLab Engine — motor Python desplegado (Railway / Render / VPS)

## Qué es esta carpeta
El motor Python (FastAPI). Viaja en el monorepo para que Railway lo despliegue con Root Directory = `engine`.

---

## Despliegue original
Envoltorio FastAPI alrededor del bot (`bot.py`, `backtest.py`, `filtros.py`, etc.) que lo expone por
HTTP/WebSocket para que la web de TradingLab (publicada en Vercel) lo use desde cualquier sitio.

## Despliegue en Railway (recomendado, gratis con límites)
1. Sube el repositorio a GitHub (esta carpeta `engine/` va dentro).
2. En [railway.app](https://railway.app) → New Project → Deploy from GitHub repo.
3. **Root Directory**: `engine` (subcarpeta del monorepo)
4. Railway detecta Python: si no, usa "Custom start command":
   `pip install -r requirements.txt && uvicorn web_service:app --host 0.0.0.0 --port $PORT`
   (`iqoptionapi` ya está en `requirements.txt`).
5. Variables de entorno (en Railway → Variables):
   - `CLIENT_URL`: `https://tu-web.vercel.app` (CORS, sin barra final)
   - `GROQ_API_KEY` o `GEMINI_API_KEY`: tu clave de IA (opcional, el filtro IA)
   - `IQ_EMAIL` / `IQ_PASSWORD`: opcional. Permiten que el Backtest descargue el histórico real de IQ Option
     sin enviar credenciales desde el navegador.
   - `TRADINGLAB_DATA_DIR`: carpeta de un **volumen persistente** (Railway → Volumes, p. ej. `/data`).
     Sin esto el historial de operaciones se borra en cada despliegue y nunca llegarás a las 100-200
     operaciones que hacen falta para evaluar una estrategia.
6. Genera un dominio público: Settings → Networking → Generate Domain.
7. En la web de TradingLab → pestaña **Configuración** → campo "URL del motor",
   pon la URL de Railway (p. ej. `https://tradinglab-engine.up.railway.app`) y guarda.

## Despliegue en Render (alternativa)
- New → Web Service → repo + root directory `engine`
- Build: `pip install -r requirements.txt`
- Start: `uvicorn web_service:app --host 0.0.0.0 --port $PORT`
- Mismas variables (`CLIENT_URL`, `TRADINGLAB_DATA_DIR` con un disco persistente).

## Despliegue en un VPS propio
```bash
cd engine
pip install -r requirements.txt
python test_motor.py        # opcional: comprueba que todo funciona
uvicorn web_service:app --host 0.0.0.0 --port 8100
# detrás de nginx/caddy con HTTPS (obligatorio: la web de Vercel es https
# y los navegadores bloquean websocket inseguro desde https).
```

## Notas importantes
- **CORS**: `CLIENT_URL` debe ser la URL exacta de tu web en Vercel
  (p. ej. `https://tradinglab-tu-usuario.vercel.app`), sin barra final.
- **WebSocket**: el motor difunde eventos de trading por `/ws` (difusión a todos los clientes).
- **iqoptionapi**: fork no oficial, se instala desde GitHub (ya está en `requirements.txt`). No es la API oficial de IQ Option.
- **Sesión de IQ Option**: el login lo hace la web (pestaña Conexión) contra este motor;
  usa cuenta DEMO salvo que sepas lo que haces.

## Cómo se evalúa una estrategia (resumen)
1. **Backtest** (pestaña Backtest): usa datos de IQ Option, no sintéticos. Walk-forward + p-valor ajustado (Holm).
   Con datos sintéticos el veredicto siempre advierte que no vale nada.
2. **Demo**: una estrategia fija, 100-200 operaciones en cuenta DEMO. La tarjeta «Evidencia y condiciones» (pestaña
   Motor Bot) las compara con el punto de equilibrio. Las operaciones simuladas no cuentan.
3. Si no supera el equilibrio con claridad, no se pasa a dinero real. «Ninguna estrategia gana» es un resultado válido.
