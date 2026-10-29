# TradingLab

Laboratorio web para crear, probar y comparar estrategias de trading sin programar. Generas lotes de estrategias con reglas al azar, las pruebas sobre un histórico de precios simulados con curva de capital y métricas, optimizas sus parámetros y exportas la que más te guste en pseudocódigo.

Inspirada en las funciones de StrategyQuant X. No es una app oficial ni contiene nada del original; mira [LEGAL.md](LEGAL.md).

## Funciones

- **Generador de estrategias**: crea un lote de 10 estrategias con reglas de entrada y salida al azar (cruce de medias, rebote del RSI, ruptura de rango) con stop loss y objetivo de beneficio.
- **Backtesting**: prueba cada estrategia sobre 400 velas de mercado simuladas y muestra la curva de capital, el beneficio, el drawdown máximo, el porcentaje de aciertos y el número de operaciones. Puedes cambiar el capital inicial y volver a probar.
- **Optimizador**: prueba variaciones de los parámetros (stop, objetivo, velas máximas, período del indicador) y te dice si alguna combinación mejora la puntuación, con opción de aplicarla con un clic.
- **Ranking de estrategias**: todas tus estrategias ordenadas por puntuación, beneficio, drawdown o porcentaje de aciertos; haz clic en una fila para seleccionarla.
- **Exportar a pseudocódigo**: descarga la estrategia elegida como archivo de texto con sus reglas y valores, o cópiala al portapapeles.

## Probarla en tu ordenador

Desde la carpeta de ClonaKit:

```bash
npm run probar -- trading-lab
```

Se abrirá en http://localhost:3000.

## Publicarla

Es una web estática (HTML, CSS y JavaScript): se publica en Vercel importando este repositorio, con el preset **Other** y sin comando de build. Cada `git push` publica una nueva versión.

## Decisiones

- Los precios son **simulados** (caminata aleatoria con pequeñas tendencias), deterministas: la misma serie en cada visita para que los resultados sean repetibles. La ficha contemplaba pegar precios propios en CSV; se ha dejado para una versión futura.
- Las estrategias no evolucionan por generación (algoritmo genético): se generan de forma independiente y el optimizador se encarga de refinarlas.
- Todo el estado vive en `localStorage` (clave `tradinglab.v1`), así que las estrategias sobreviven a recargar la página sin backend.
- La puntuación combinada pondera el beneficio relativo, castiga el drawdown y premia el porcentaje de aciertos.

## Motor Python (`engine/`)

La lógica completa del bot de escritorio (estrategias, backtesting con fricciones,
filtro IA, broker IQ Option) vive como servicio FastAPI en `engine/`. Se despliega
por separado (Railway/Render) y la web se conecta a él desde la pestaña
Configuración → "URL del motor". Instrucciones en [`engine/DESPLEGAR.md`](engine/DESPLEGAR.md).
