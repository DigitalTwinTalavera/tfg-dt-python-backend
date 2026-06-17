# Profiling y observación de rendimiento

Este documento recopila los comandos listos para responder las preguntas
del benchmark de rendimiento sin tener que recordar flags.

## 0. Antes de profilear

1. Arranca el backend en modo no-reload:

   ```bash
   uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
   ```

2. En otra terminal, lanza el script de carga:

   ```bash
   python scripts/load_test.py --vehicles 500,1000,2000,4000 --duration-s 300
   ```

3. Las métricas vivas están en:

   - `http://localhost:8000/metrics` — Prometheus exposition format.
   - `http://localhost:8000/api/simulation/metrics` — JSON estructurado con
     percentiles (`p50/p95/p99`) por subsistema.

4. El backend ya activa `loop.slow_callback_duration = 0.05` al startup, así
   que cualquier callback que bloquee el event loop > 50 ms aparece como
   `WARNING:asyncio:Executing <Task ...> took 0.07 seconds` en el log.

## 1. py-spy — sampling profiler sin reiniciar

`py-spy` se attachea al proceso ya corriendo. No requiere reiniciar ni
modificar el código. Necesita root (CAP_SYS_PTRACE) en Linux.

Instalar (en el venv del backend):

```bash
pip install py-spy
```

### Flamegraph de 60 s

```bash
sudo $(which py-spy) record \
  -o reports/profile_$(date +%Y%m%d_%H%M).svg \
  --pid $(pgrep -f "uvicorn app.main:app") \
  --duration 60 \
  --rate 100 \
  --subprocesses
```

Abre el SVG resultante en cualquier navegador. Las funciones más anchas
son las que más CPU consumen (auto-scrolleable, clickable). `--subprocesses`
captura también los workers de `ProcessPoolExecutor`.

### Top en vivo

```bash
sudo $(which py-spy) top --pid $(pgrep -f "uvicorn app.main:app")
```

Equivalente a `htop` pero por funciones Python.

### Dump de stacks (diagnóstico de GIL / cuelgues)

```bash
sudo $(which py-spy) dump --pid $(pgrep -f "uvicorn app.main:app")
```

Imprime el stack actual de cada thread/task. Útil para responder "¿dónde
está bloqueado el proceso?" — si la mayoría de los threads están en
`epoll_wait` o `select`, el cuello no es CPU sino IO. Si ves `_advance_vehicle_idm`
en N threads simultáneos, el GIL está causando contención.

## 2. scalene — CPU + memoria por línea

Más invasivo (hay que lanzarlo en lugar de Python), pero da granularidad
por línea: cuánto tiempo CPU vs tiempo wall-clock vs memoria.

```bash
pip install scalene

scalene --no-browser --html --outfile reports/scalene_$(date +%Y%m%d_%H%M).html \
        --profile-only "app/core,app/services" \
        --cpu --memory \
        -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Después de pararlo (Ctrl-C tras la sesión de carga), abre el HTML. Cada
línea de los archivos en `app/core` y `app/services` muestra:

- `% CPU` (tiempo de CPU efectivo) — si `% time` se acerca a `% CPU`, está
  CPU-bound puro.
- `% time` (wall-clock) — si es alto pero `% CPU` bajo, está IO-bound o
  bloqueado en GIL.
- Memory delta — útil para detectar copias innecesarias.

## 3. Detección de O(n²)

El script `scripts/load_test.py` imprime al final una tabla con
`physics_us/vehicle` y el ratio entre tamaños consecutivos. Esperado:

- O(N): ratio ≈ 1.0 al duplicar N.
- O(N log N): ratio ≈ 1.1 al duplicar N.
- O(N²): ratio ≈ 2.0 al duplicar N → bandera "⚠ posible O(n²)".

Si la tabla flagea O(n²), el siguiente paso es perfilar **solo ese
stage** con py-spy `--rate 250 --duration 90` para localizar la función
responsable.

## 4. Uso de CPU por core / thread

```bash
pidstat -h -p $(pgrep -f "uvicorn app.main:app") -t 1   # threads
pidstat -h -p $(pgrep -f "uvicorn app.main:app")    1   # proceso
htop -p $(pgrep -f uvicorn)                              # top interactivo
```

`pidstat -t` muestra el % CPU por thread Python. Bajo GIL, solo verás un
thread con CPU significativa salvo durante `update_vehicles_parallel`,
donde los workers de `ProcessPoolExecutor` (procesos separados) sí
saturan varios cores.

## 5. Bytes WebSocket por tick / segundo

Sin tooling extra: el endpoint JSON ya lo expone.

```bash
curl -s http://localhost:8000/api/simulation/metrics | jq '
  {
    bytes_per_tick_p95: .histograms_ms["ws.bytes_per_tick"].p95,
    bytes_per_tick_p99: .histograms_ms["ws.bytes_per_tick"].p99,
    bytes_per_s:        .derived.ws_bytes_per_second,
    chunks_p95:         .histograms_ms["ws.chunks_per_tick"].p95,
    clients:            .gauges["ws.clients"]
  }
'
```

Para ver la evolución durante una sesión de carga:

```bash
while true; do
  curl -s http://localhost:8000/api/simulation/metrics |
    jq -r '[now, .derived.vehicles_active, .derived.ws_bytes_per_second] | @tsv'
  sleep 5
done
```
