# TradingLab Engine — motor Python desplegado (Railway / Render / VPS)

## Qué es esta carpeta
Copia del motor que vive en `bot-desktop/` (fuente de verdad de desarrollo).
Viaja en el monorepo para que Railway lo despliegue con Root Directory = `engine`.

---

## Despliegue original
Envoltorio FastAPI alrededor de tu bot original (`bot.py`, `engine.py`, etc.).
No modifica la lógica: la expone por HTTP/WebSocket para que la web de TradingLab
(publicada en Vercel) la use desde cualquier sitio.

## Despliegue en Railway (recomendado, gratis con límites)
1. Sube `bot-desktop/` a un repo de GitHub (puede ser el mismo del clon, en esta carpeta).
2. En [railway.app](https://railway.app) → New Project → Deploy from GitHub repo.
3. **Root Directory**: `engine` (subcarpeta del monorepo)
4. Railway detecta Python: si no, usa "Custom start command":
   `pip install -r requirements.txt && pip install git+https://github.com/Lu-Yi-Hsun/iqoptionapi.git && uvicorn web_service:app --host 0.0.0.0 --port $PORT`
5. Variables de entorno (en Railway → Variables):
   - `CLONE_URL`: `https://tu-clon.vercel.app` (CORS)
   - `TRADINGLAB_AI_KEY`: tu clave de Groq o Gemini (opcional, el filtro IA)
6. Genera un dominio público: Settings → Networking → Generate Domain.
7. En la web de TradingLab → pestaña **Configuración** → campo "URL del motor",
   pon la URL de Railway (p. ej. `https://tradinglab-engine.up.railway.app`) y guarda.

## Despliegue en Render (alternativa)
- New → Web Service → repo + root directory `engine`
- Build: `pip install -r requirements.txt && pip install git+https://github.com/Lu-Yi-Hsun/iqoptionapi.git`
- Start: `uvicorn web_service:app --host 0.0.0.0 --port $PORT`
- Misma variable `CLONE_URL`.

## Despliegue en un VPS propio
```bash
cd bot-desktop
pip install -r requirements.txt
pip install git+https://github.com/Lu-Yi-Hsun/iqoptionapi.git
uvicorn web_service:app --host 0.0.0.0 --port 8100
# detrás de nginx/caddy con HTTPS (obligatorio: la web de Vercel es https
# y los navegadores bloquean websocket inseguro desde https).
```

## Notas importantes
- **CORS**: `CLONE_URL` debe ser la URL exacta de tu web en Vercel
  (p. ej. `https://tradinglab-tu-usuario.vercel.app`), sin barra final.
- **WebSocket**: el motor difunde eventos de trading por `/ws` (difusión a todos los clientes).
- **iqoptionapi**: se instala desde GitHub porque la versión de PyPI está desactualizada.
- **Sesión de IQ Option**: el login lo hace la web (pestaña Conexión) contra este motor;
  usa cuenta DEMO salvo que sepas lo que haces.
