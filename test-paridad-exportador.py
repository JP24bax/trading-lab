"""Apoyo de test-motor-binario.mjs: ejecuta los .py que genera el exportador y comprueba que dan la MISMA señal
que el evaluador JS en las mismas velas. Uso interno: python test-paridad-exportador.py <casos.json>"""
import json
import sys
import types

import numpy as np
import pandas as pd

casos = json.load(open(sys.argv[1], encoding="utf-8"))
try:
    import pandas_ta  # noqa: F401
except ImportError:
    print(json.dumps({"saltado": "pandas_ta no está instalado"}))
    sys.exit(0)

# iqoptionapi no hace falta para evaluar señales: se sustituye por un módulo vacío.
stub = types.ModuleType("iqoptionapi.stable_api")
stub.IQ_Option = object
sys.modules["iqoptionapi"] = types.ModuleType("iqoptionapi")
sys.modules["iqoptionapi.stable_api"] = stub

cierres = np.array(casos["cierres"], dtype=float)
previo = np.r_[cierres[0], cierres[:-1]]
base = pd.DataFrame({"open": previo, "high": np.maximum(cierres, previo), "low": np.minimum(cierres, previo), "close": cierres})

total = coinciden = con_senal_js = recuperadas = 0
errores = []
for caso in casos["casos"]:
    ns = {"__name__": "generado"}
    try:
        exec(compile(caso["codigo"], "generado.py", "exec"), ns)
    except Exception as e:  # el .py debe al menos compilar y cargar
        errores.append(f"semilla {caso['semilla']}: no carga ({type(e).__name__}: {e})")
        continue
    for k, esperado in caso["posiciones"]:
        ventana = base.iloc[max(0, k - 299):k + 1].copy()
        try:
            obtenido = ns["hay_senal"](ns["calcular_indicadores"](ventana))
        except Exception as e:
            errores.append(f"semilla {caso['semilla']} vela {k}: {type(e).__name__}: {e}")
            break
        total += 1
        coinciden += obtenido == esperado
        if esperado:
            con_senal_js += 1
            recuperadas += obtenido == esperado
print(json.dumps({"total": total, "coinciden": coinciden, "con_senal_js": con_senal_js,
                  "recuperadas": recuperadas, "errores": errores[:5]}))
