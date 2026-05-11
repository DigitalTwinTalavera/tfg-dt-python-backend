"""
Primitivas de instrumentación de rendimiento.

Centraliza:
  - MetricsRegistry: ring-buffer histograms + counters + gauges, con snapshot
    JSON y formato Prometheus exposition.
  - SplitTimer: cronómetro acumulador para el for principal de física.
    Una sola lectura de perf_counter_ns por transición (no entry+exit por
    subsistema). Pensado para mantener overhead por debajo del 5% incluso
    a 4000 vehículos × 5 Hz.
  - time_block: context manager para medir bloques fuera del hot path.

Sin locks: el tick es single-thread async; los reportes se generan bajo
demanda desde el mismo event loop. Si prometheus_client está instalado,
las observaciones se duplican ahí para soporte nativo de scraping.
"""

from __future__ import annotations

import math
import time
from collections import deque
from contextlib import contextmanager
from typing import Iterator

try:
    import prometheus_client as _prom
    _PROM_AVAILABLE = True
except ImportError:
    _prom = None  # type: ignore[assignment]
    _PROM_AVAILABLE = False


_DEFAULT_BUFFER_SIZE = 2048

_PROM_BUCKETS_SECONDS: tuple[float, ...] = (
    0.0001, 0.0005, 0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0,
)


_PROM_NAME_TRANS = str.maketrans({".": "_", "-": "_"})


def _prom_name(name: str) -> str:
    """Convierte nombres tipo `phys.idm_ms` → `phys_idm_ms` (Prometheus
    no admite '.' ni '-' en nombres de métrica)."""
    return name.translate(_PROM_NAME_TRANS)


def _percentile(sorted_arr: list[float], q: float) -> float:
    """Linear-interpolation percentile sobre lista ya ordenada."""
    n = len(sorted_arr)
    if n == 0:
        return 0.0
    if n == 1:
        return sorted_arr[0]
    k = (n - 1) * q
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_arr[int(k)]
    return sorted_arr[f] * (c - k) + sorted_arr[c] * (k - f)


class MetricsRegistry:
    """
    Tres familias: histogramas (ring buffer N=2048 en ms), counters
    (monotónicos) y gauges (último valor). Snapshot calcula percentiles bajo
    demanda; reset() limpia solo histogramas para no romper invariantes de
    Prometheus en counters.
    """

    def __init__(self, buffer_size: int = _DEFAULT_BUFFER_SIZE) -> None:
        self._buffer_size = buffer_size
        self._hist: dict[str, deque[float]] = {}
        self._counters: dict[str, float] = {}
        self._gauges: dict[str, float] = {}
        self._prom_hists: dict[str, object] = {}
        self._prom_counters: dict[str, object] = {}
        self._prom_gauges: dict[str, object] = {}

    # ---- Histograms (record en ms) ----
    def record(self, name: str, value_ms: float) -> None:
        d = self._hist.get(name)
        if d is None:
            d = deque(maxlen=self._buffer_size)
            self._hist[name] = d
        d.append(value_ms)
        if _PROM_AVAILABLE:
            h = self._prom_hists.get(name)
            if h is None:
                h = _prom.Histogram(  # type: ignore[union-attr]
                    f"sim_{_prom_name(name)}_seconds",
                    f"Histogram for {name} (seconds).",
                    buckets=_PROM_BUCKETS_SECONDS,
                )
                self._prom_hists[name] = h
            h.observe(value_ms / 1000.0)  # type: ignore[attr-defined]

    def record_many(self, samples_ms: dict[str, float]) -> None:
        for name, v in samples_ms.items():
            self.record(name, v)

    # ---- Counters (monotónicos) ----
    def inc(self, name: str, by: float = 1.0) -> None:
        self._counters[name] = self._counters.get(name, 0.0) + by
        if _PROM_AVAILABLE:
            c = self._prom_counters.get(name)
            if c is None:
                c = _prom.Counter(  # type: ignore[union-attr]
                    f"sim_{_prom_name(name)}_total",
                    f"Counter for {name}.",
                )
                self._prom_counters[name] = c
            c.inc(by)  # type: ignore[attr-defined]

    # ---- Gauges (último valor) ----
    def gauge(self, name: str, value: float) -> None:
        self._gauges[name] = value
        if _PROM_AVAILABLE:
            g = self._prom_gauges.get(name)
            if g is None:
                g = _prom.Gauge(  # type: ignore[union-attr]
                    f"sim_{_prom_name(name)}",
                    f"Gauge for {name}.",
                )
                self._prom_gauges[name] = g
            g.set(value)  # type: ignore[attr-defined]

    # ---- Snapshot ----
    def snapshot(self) -> dict:
        hist_out: dict[str, dict[str, float]] = {}
        for name, buf in self._hist.items():
            if not buf:
                continue
            arr = sorted(buf)
            hist_out[name] = {
                "count": len(arr),
                "p50": _percentile(arr, 0.50),
                "p95": _percentile(arr, 0.95),
                "p99": _percentile(arr, 0.99),
                "max": arr[-1],
                "mean": sum(arr) / len(arr),
            }
        return {
            "histograms_ms": hist_out,
            "counters": dict(self._counters),
            "gauges": dict(self._gauges),
        }

    def reset_histograms(self) -> None:
        """Limpia ring buffers (no toca counters/gauges)."""
        for d in self._hist.values():
            d.clear()

    def prometheus_text(self) -> str:
        if _PROM_AVAILABLE:
            return _prom.generate_latest().decode("utf-8")  # type: ignore[union-attr]
        # Fallback manual: exposition format simple sin la lib.
        lines: list[str] = []
        snap = self.snapshot()
        for name, stats in snap["histograms_ms"].items():
            mname = f"sim_{_prom_name(name)}_milliseconds"
            lines.append(f"# HELP {mname} Summary (ms) for {name}.")
            lines.append(f"# TYPE {mname} summary")
            for q_label, key in (("0.5", "p50"), ("0.95", "p95"), ("0.99", "p99")):
                lines.append(f'{mname}{{quantile="{q_label}"}} {stats[key]}')
            lines.append(f"{mname}_count {stats['count']}")
            lines.append(f"{mname}_sum {stats['mean'] * stats['count']}")
        for name, v in snap["counters"].items():
            mname = f"sim_{_prom_name(name)}_total"
            lines.append(f"# HELP {mname} Counter for {name}.")
            lines.append(f"# TYPE {mname} counter")
            lines.append(f"{mname} {v}")
        for name, v in snap["gauges"].items():
            mname = f"sim_{_prom_name(name)}"
            lines.append(f"# HELP {mname} Gauge for {name}.")
            lines.append(f"# TYPE {mname} gauge")
            lines.append(f"{mname} {v}")
        return "\n".join(lines) + "\n"


registry = MetricsRegistry()


class SplitTimer:
    """
    Cronómetro acumulador para hot loops.

    En cada `split(name)` suma al bucket `name` el tiempo desde la última
    llamada (o desde la construcción). Una sola lectura de perf_counter_ns
    por transición — no se mide entrada+salida por subsistema.

    Uso:
        st = SplitTimer()
        for v in vehicles:
            do_a(v); st.split("a")
            do_b(v); st.split("b")
        st.emit("phys.")    # registry.record("phys.a", ms), ...
    """

    __slots__ = ("_t", "acc")

    def __init__(self) -> None:
        self._t = time.perf_counter_ns()
        self.acc: dict[str, int] = {}

    def split(self, name: str) -> None:
        now = time.perf_counter_ns()
        self.acc[name] = self.acc.get(name, 0) + (now - self._t)
        self._t = now

    def mark(self) -> None:
        """
        Resetea solo el cronómetro sin tocar acumuladores. Útil al inicio de
        cada iteración para que el overhead de control de flujo entre splits
        no se atribuya al primer subsistema medido.
        """
        self._t = time.perf_counter_ns()

    def restart(self) -> None:
        """Resetea cronómetro y acumulador (para reciclar entre ticks)."""
        self._t = time.perf_counter_ns()
        self.acc = {}

    def emit(self, prefix: str = "") -> None:
        for name, ns in self.acc.items():
            registry.record(f"{prefix}{name}", ns / 1_000_000.0)
        self.acc = {}
        self._t = time.perf_counter_ns()


@contextmanager
def time_block(name: str) -> Iterator[None]:
    """Context manager para medir un bloque y registrar como histograma (ms)."""
    t0 = time.perf_counter_ns()
    try:
        yield
    finally:
        registry.record(name, (time.perf_counter_ns() - t0) / 1_000_000.0)
