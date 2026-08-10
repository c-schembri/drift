"""Public Drift error taxonomy."""


class DriftError(RuntimeError):
    """Base class for expected project and execution failures."""


class ConfigurationError(DriftError):
    """Raised when project configuration or a declared graph is invalid."""


class ExecutionError(DriftError):
    """Raised when an action cannot be executed successfully."""


class BootstrapError(DriftError):
    """Raised when a pinned tool cannot be resolved safely."""
