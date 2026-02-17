"""
Excepciones personalizadas para el motor de simulación.
"""


class SimulationError(Exception):
    """Excepción base para errores de simulación."""


class SimulationStateError(SimulationError):
    """Error de transición de estado inválida en la simulación."""


class SimulationAlreadyRunningError(SimulationStateError):
    """La simulación ya está en ejecución."""

    def __init__(self) -> None:
        super().__init__("La simulación ya está en ejecución")


class SimulationNotRunningError(SimulationStateError):
    """La simulación no está en ejecución."""

    def __init__(self) -> None:
        super().__init__("La simulación no está en ejecución")


class SimulationNotPausedError(SimulationStateError):
    """La simulación no está pausada."""

    def __init__(self) -> None:
        super().__init__("La simulación no está pausada")
