"""Centralised, validated configuration for a digital twin instance.

Sub-configs are plain BaseModel so they compose cleanly inside TwinConfig
(a BaseSettings).  TwinConfig reads nested values via double-underscore env
vars, e.g.  DT_MQTT__BROKER=mybroker  or a JSON blob DT_MQTT='{"broker":"…"}'.
"""

from __future__ import annotations

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class MQTTConfig(BaseModel):
    broker: str = "localhost"
    port: int = 1883
    keepalive: int = 60
    username: str = ""
    password: str = ""
    tls: bool = False              # enable TLS (typically with port=8883)
    tls_ca_certs: str = ""         # CA bundle path; "" = system default CAs
    tls_insecure: bool = False     # skip hostname verification (test brokers only)


class InfluxConfig(BaseModel):
    url: str = "http://localhost:8086"
    token: str = "my-super-secret-token"
    org: str = "digital_twin"
    bucket: str = "asset_telemetry"


class MongoConfig(BaseModel):
    uri: str = "mongodb://admin:password@localhost:27017"
    db: str = "digital_twin"


class RedisConfig(BaseModel):
    url: str = "redis://localhost:6379"
    db: int = 0


class MinIOConfig(BaseModel):
    endpoint: str = "localhost:9000"
    access_key: str = "minioadmin"
    secret_key: str = "minioadmin"
    secure: bool = False
    bucket: str = "digital-twin-assets"


class DittoConfig(BaseModel):
    url: str = "http://localhost:8080"
    user: str = "ditto"
    password: str = "ditto"
    namespace: str = "org.example"


class Neo4jConfig(BaseModel):
    uri: str = "bolt://localhost:7687"
    user: str = "neo4j"
    password: str = "password"


class LLMConfig(BaseModel):
    provider: str = "openai"        # "openai" | "anthropic" | "ollama"
    model: str = "gpt-4o-mini"
    api_key: str = ""
    base_url: str = ""              # for Ollama or custom deployments
    temperature: float = 0.0
    timeout_s: float = 60.0         # per-request timeout
    max_tokens: int = 2048          # response cap (Ollama: num_predict)
    max_retries: int = 2            # client-level retries on transient errors


class SensorFieldSpec(BaseModel):
    name: str
    unit: str = ""
    nominal: float | None = None     # None = computed/derived field; GenericSimulator skips it
    noise_std: float = 0.01
    warn_threshold: float | None = None
    crit_threshold: float | None = None
    threshold_direction: str = "high"   # "high" = alert above, "low" = alert below


class SecurityConfig(BaseModel):
    """Deployment security posture.

    ``mode="dev"`` keeps the zero-config local experience: default
    credentials, open CORS, no API key. ``mode="production"`` makes the twin
    refuse to start unless every check in
    :func:`dyon.core.security.assert_production_safe` passes.
    """
    mode: str = "dev"                    # "dev" | "production"
    api_key: str = ""                    # non-empty => all /api/* routes require it
    cors_origins: list[str] = []         # explicit origins; [] = same-origin only in production


class TwinConfig(BaseSettings):
    """Root configuration for a single digital twin instance.

    Environment variables use the ``DT_`` prefix.  Nested fields use the
    double-underscore delimiter, e.g. ``DT_MQTT__BROKER=myhost``.

    A full ``.env`` file can also be used (loaded by python-dotenv before
    constructing this object, or via ``_env_file`` kwarg).
    """

    model_config = SettingsConfigDict(
        env_prefix="DT_",
        env_nested_delimiter="__",
        extra="ignore",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    asset_id: str = "asset_001"
    asset_type: str = "generic_asset"
    asset_name: str = "My Asset"

    # Sensor fields — the user defines these; everything else adapts
    sensor_fields: list[SensorFieldSpec] = []

    # Infrastructure sub-configs (populated from DT_MQTT__*, DT_INFLUX__*, …)
    mqtt: MQTTConfig = Field(default_factory=MQTTConfig)
    influx: InfluxConfig = Field(default_factory=InfluxConfig)
    mongo: MongoConfig = Field(default_factory=MongoConfig)
    redis: RedisConfig = Field(default_factory=RedisConfig)
    minio: MinIOConfig = Field(default_factory=MinIOConfig)
    ditto: DittoConfig = Field(default_factory=DittoConfig)
    neo4j: Neo4jConfig = Field(default_factory=Neo4jConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)

    # API server
    api_host: str = "127.0.0.1"   # set DT_API_HOST=0.0.0.0 explicitly to expose beyond localhost
    api_port: int = 8500

    @property
    def topic_telemetry(self) -> str:
        return f"dt/{self.asset_id}/telemetry"

    @property
    def topic_control(self) -> str:
        return f"dt/{self.asset_id}/control"

    @property
    def topic_state(self) -> str:
        return f"dt/{self.asset_id}/state"

    @property
    def thing_id(self) -> str:
        return f"{self.ditto.namespace}:{self.asset_id}"

    @property
    def field_names(self) -> list[str]:
        return [f.name for f in self.sensor_fields]

    @property
    def thresholds(self) -> dict:
        # Include a field if EITHER bound is set; a missing bound stays None and
        # consumers skip just that check. Requiring both would silently ignore a
        # field configured with only a warn (or only a crit) level.
        result = {}
        for f in self.sensor_fields:
            if f.warn_threshold is None and f.crit_threshold is None:
                continue
            result[f.name] = {
                "warn": f.warn_threshold,
                "crit": f.crit_threshold,
                "low": f.threshold_direction == "low",
            }
        return result

    @property
    def field_specs(self) -> dict[str, SensorFieldSpec]:
        return {f.name: f for f in self.sensor_fields}
