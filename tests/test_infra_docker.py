"""Generated docker-compose honours configured credentials, not literal defaults.

The Influx admin password is generated (never the literal "password"), Mongo root
credentials come from the configured URI, and mosquitto only runs anonymously
when no MQTT username is set.
"""

from __future__ import annotations

from dyon.core.config import TwinConfig
from dyon.infra.docker import DockerComposeGenerator


def _compose(config, layers=("network", "data")):
    return DockerComposeGenerator().generate(config, list(layers))


def test_influx_admin_password_is_not_the_literal_default():
    cfg = TwinConfig(influx={"token": "custom-token"})
    compose = _compose(cfg)
    assert "custom-token" in compose                 # configured token threaded through
    assert 'DOCKER_INFLUXDB_INIT_PASSWORD: password' not in compose
    assert 'DOCKER_INFLUXDB_INIT_PASSWORD: "password"' not in compose


def test_mongo_credentials_come_from_configured_uri():
    cfg = TwinConfig(mongo={"uri": "mongodb://root:s3cretpw@db:27017"})
    compose = _compose(cfg)
    assert "s3cretpw" in compose
    assert "MONGO_INITDB_ROOT_USERNAME: root" in compose


def test_mosquitto_anonymous_only_without_username(tmp_path):
    gen = DockerComposeGenerator()
    # No username -> anonymous broker.
    conf = gen._mosquitto_conf(TwinConfig())
    assert "allow_anonymous true" in conf
    # Username set -> anonymous disabled, password file referenced.
    conf2 = gen._mosquitto_conf(TwinConfig(mqtt={"username": "sensor"}))
    assert "allow_anonymous false" in conf2
    assert "password_file" in conf2


def test_minio_credentials_are_configured():
    cfg = TwinConfig(minio={"access_key": "myaccess", "secret_key": "mysecret"})
    compose = _compose(cfg)
    assert "myaccess" in compose and "mysecret" in compose
