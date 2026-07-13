"""Production-mode safety checks: refuse to start insecurely configured twins."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dyon.core.config import TwinConfig

# Known factory defaults. A production twin must not run with any of these.
_INSECURE_DEFAULTS: dict[str, str] = {
    "influx.token": "my-super-secret-token",
    "mongo.uri": "mongodb://admin:password@localhost:27017",
    "minio.access_key": "minioadmin",
    "minio.secret_key": "minioadmin",
    "ditto.password": "ditto",
    "neo4j.password": "password",
}


class InsecureConfigError(RuntimeError):
    """Raised when a production-mode twin is configured with insecure defaults."""


def find_insecure_settings(config: TwinConfig) -> list[str]:
    """Return human-readable descriptions of every insecure setting found."""
    problems: list[str] = []
    for path, default in _INSECURE_DEFAULTS.items():
        section, attr = path.split(".")
        if getattr(getattr(config, section), attr) == default:
            problems.append(f"{path} is still the factory default")
    if not config.security.api_key:
        problems.append("security.api_key is empty — every endpoint would be open")
    if "*" in config.security.cors_origins:
        problems.append("security.cors_origins contains '*'")
    if config.api_host == "0.0.0.0" and not config.security.api_key:
        problems.append("api_host binds all interfaces without an API key")
    return problems


def assert_production_safe(config: TwinConfig) -> None:
    """No-op in dev mode; raise InsecureConfigError in production mode."""
    if config.security.mode != "production":
        return
    problems = find_insecure_settings(config)
    if problems:
        raise InsecureConfigError(
            "Refusing to start in production mode:\n  - " + "\n  - ".join(problems)
            + "\nFix each item via DT_* environment variables or set "
            "DT_SECURITY__MODE=dev for local development."
        )
