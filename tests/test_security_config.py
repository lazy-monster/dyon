"""Production-mode safety checks refuse to start an insecurely configured twin.

Dev mode (the default) keeps the zero-config local experience; production mode
enumerates every insecure default and raises before the twin can run.
"""

from __future__ import annotations

import pytest

from dyon.core.config import TwinConfig
from dyon.core.security import (
    InsecureConfigError,
    assert_production_safe,
    find_insecure_settings,
)


def test_dev_mode_starts_with_defaults():
    # The whole point of dev mode: default credentials and no key are tolerated.
    assert_production_safe(TwinConfig())  # does not raise


def test_defaults_are_all_flagged():
    problems = find_insecure_settings(TwinConfig())
    # Six factory-default credentials + the empty API key.
    assert len(problems) >= 7
    joined = "\n".join(problems)
    for token in ("influx.token", "mongo.uri", "minio.access_key",
                  "minio.secret_key", "ditto.password", "neo4j.password",
                  "security.api_key"):
        assert token in joined


def test_production_mode_raises_listing_every_problem():
    cfg = TwinConfig(security={"mode": "production"})
    with pytest.raises(InsecureConfigError) as exc:
        assert_production_safe(cfg)
    msg = str(exc.value)
    assert "influx.token" in msg and "api_key" in msg


def test_wildcard_cors_is_rejected_in_production():
    cfg = TwinConfig(security={"mode": "production", "cors_origins": ["*"]})
    assert any("cors_origins" in p for p in find_insecure_settings(cfg))


def test_clean_production_config_passes():
    cfg = TwinConfig(
        security={"mode": "production", "api_key": "a-real-long-key",
                  "cors_origins": ["https://dash.example.com"]},
        influx={"token": "real-token"},
        mongo={"uri": "mongodb://user:realpw@db:27017"},
        minio={"access_key": "realkey", "secret_key": "realsecret"},
        ditto={"password": "realpw"},
        neo4j={"password": "realpw"},
    )
    assert find_insecure_settings(cfg) == []
    assert_production_safe(cfg)  # does not raise


def test_api_host_default_is_localhost():
    assert TwinConfig().api_host == "127.0.0.1"
