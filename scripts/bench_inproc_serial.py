#!/usr/bin/env python3
"""Línea base monohilo de bench_inproc: fuerza el camino serial de la física
(un único hilo auxiliar) elevando el umbral de paralelización ANTES de que
vehicle_physics lo importe. Mismo motor, mismo build free-threaded; la única
diferencia es que el cómputo no se reparte en el grupo de hilos.

Uso:
    PYTHON_GIL=0 .venv/bin/python scripts/bench_inproc_serial.py \
        --vehicles 1000,2000,4000,6000 --warmup-s 20 --measure-s 70 \
        --out reports/bench/inproc_serial.json
"""
import runpy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import app.core.constants as constants  # noqa: E402

constants.VEHICLE_PHYSICS_PARALLEL_THRESHOLD = 10**9  # nunca paralelizar

runpy.run_path(str(Path(__file__).parent / "bench_inproc.py"), run_name="__main__")
