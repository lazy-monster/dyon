"""DockerComposeGenerator: generates docker-compose.yml for Dyon services."""

from __future__ import annotations

import hashlib
import logging
import secrets
from pathlib import Path
from typing import TYPE_CHECKING

try:
    import yaml
    _YAML = True
except ImportError:
    _YAML = False

if TYPE_CHECKING:
    from dyon.core.config import TwinConfig

log = logging.getLogger(__name__)


_ITOA64 = "./0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


def _to64(value: int, n: int) -> str:
    result = []
    for _ in range(n):
        result.append(_ITOA64[value & 0x3f])
        value >>= 6
    return "".join(result)


def _apr1_hash(password: str, salt: str | None = None) -> str:
    """Return an Apache APR1-MD5 password hash for use in nginx htpasswd files."""
    if salt is None:
        salt = secrets.token_hex(4)  # 8 hex chars — valid htpasswd salt chars

    pw = password.encode()
    sp = salt.encode()
    magic = b"$apr1$"

    # Digest A: password + magic + salt
    ctx_a = hashlib.md5(pw + magic + sp)

    # Digest B: password + salt + password
    sum_b = hashlib.md5(pw + sp + pw).digest()

    # Append bytes from B to A, cycling through len(password) bytes
    i = len(pw)
    while i > 0:
        ctx_a.update(sum_b[:min(i, 16)])
        i -= 16

    # Process bits of len(password), alternating null byte and first byte of pw
    i = len(pw)
    while i > 0:
        ctx_a.update(b"\x00" if (i & 1) else pw[:1])
        i >>= 1

    digest = ctx_a.digest()

    # 1000 mixing rounds
    for i in range(1000):
        ctx_c = hashlib.md5()
        ctx_c.update(pw    if (i & 1) else digest)
        if i % 3:
            ctx_c.update(sp)
        if i % 7:
            ctx_c.update(pw)
        ctx_c.update(digest if (i & 1) else pw)
        digest = ctx_c.digest()

    # Encode with APR1 byte ordering into custom base64
    s = digest
    encoded = (
        _to64((s[0]  << 16) | (s[6]  << 8) | s[12], 4)
        + _to64((s[1]  << 16) | (s[7]  << 8) | s[13], 4)
        + _to64((s[2]  << 16) | (s[8]  << 8) | s[14], 4)
        + _to64((s[3]  << 16) | (s[9]  << 8) | s[15], 4)
        + _to64((s[4]  << 16) | (s[10] << 8) | s[5],  4)
        + _to64(s[11], 2)
    )
    return f"$apr1${salt}${encoded}"


class DockerComposeGenerator:
    """Generates docker-compose.yml based on which layers are active."""

    def generate(self, config: TwinConfig, active_layers: list[str]) -> str:
        services: dict = {}
        volumes:  dict = {}
        configs:  dict = {}

        if "network" in active_layers:
            services["mosquitto"] = self._mosquitto()

        if "data" in active_layers or "network" in active_layers:
            services["influxdb"] = self._influxdb(config)
            services["mongodb"]  = self._mongodb(config)
            services["redis"]    = self._redis()
            services["minio"]    = self._minio(config)
            volumes["influxdb_data"] = None
            volumes["mongodb_data"]  = None
            volumes["redis_data"]    = None
            volumes["minio_data"]    = None

        if "services" in active_layers or "service_ditto" in active_layers:
            services.update(self._ditto_stack(config))
            configs.update(self._ditto_configs(config))
            volumes["ditto_data"] = None

        if "intelligent" in active_layers:
            services["neo4j"] = self._neo4j(config)
            volumes["neo4j_data"] = None

        services["grafana"] = self._grafana()
        volumes["grafana_data"] = None

        # Compose v2 deprecates the top-level "version" key; emit services/volumes/configs only.
        compose: dict = {"services": services}
        if volumes:
            compose["volumes"] = {k: {} for k in volumes}
        if configs:
            compose["configs"] = configs

        if not _YAML:
            import json
            return json.dumps(compose, indent=2)
        return yaml.dump(compose, default_flow_style=False, sort_keys=False)

    def _mosquitto_conf(self, config: TwinConfig | None = None) -> str:
        """Render mosquitto.conf.

        With no MQTT username configured the broker stays anonymous (the
        zero-config local default) — but that is logged loudly. When a username
        is set, anonymous access is disabled and a ``password_file`` is
        referenced; the operator must create it once with
        ``mosquitto_passwd -c mosquitto.passwd <user>``.
        """
        username = getattr(getattr(config, "mqtt", None), "username", "") if config else ""
        if username:
            auth = (
                "allow_anonymous false\n"
                "password_file /mosquitto/config/mosquitto.passwd\n"
            )
            log.warning(
                "mosquitto.conf requires a password file: run "
                "`mosquitto_passwd -c mosquitto.passwd %s` and mount it alongside "
                "mosquitto.conf before starting the broker.", username,
            )
        else:
            auth = "allow_anonymous true\n"
            log.warning(
                "mosquitto.conf emitted with anonymous access enabled "
                "(no DT_MQTT__USERNAME set) — do not expose this broker beyond a "
                "trusted network."
            )
        return (
            "listener 1883\n"
            + auth
            + "\n"
            "# WebSocket listener (browser dashboards / MQTT over WebSocket)\n"
            "listener 9001\n"
            "protocol websockets\n"
            + auth
            + "\n"
            "log_type error\n"
            "log_type warning\n"
            "log_type notice\n"
            "log_type information\n"
        )

    def write_companion_files(
        self,
        compose_path: str | Path,
        active_layers: list[str],
        config: TwinConfig | None = None,
    ) -> list[str]:
        """Write config files required by the generated docker-compose.yml.

        Returns a list of written file paths (for CLI echo).
        """
        out_dir = Path(compose_path).parent
        written = []
        if "network" in active_layers:
            mosquitto_conf = out_dir / "mosquitto.conf"
            if not mosquitto_conf.exists():
                mosquitto_conf.write_text(self._mosquitto_conf(config))
                written.append(str(mosquitto_conf))
        return written

    def _mosquitto(self) -> dict:
        return {
            "image": "eclipse-mosquitto:2",
            "ports": ["1883:1883", "9001:9001"],
            "volumes": ["./mosquitto.conf:/mosquitto/config/mosquitto.conf"],
            "restart": "unless-stopped",
        }

    def _influxdb(self, config: TwinConfig) -> dict:
        # There is no dedicated config field for the Influx admin-UI password, so
        # generate a strong one rather than shipping the literal "password". Emit
        # it once at WARNING so the operator can record it; the API token they do
        # control is threaded through as INIT_ADMIN_TOKEN.
        admin_password = secrets.token_urlsafe(24)
        log.warning(
            "Generated InfluxDB admin password for compose: %s — record it now", admin_password
        )
        return {
            "image": "influxdb:2",
            "ports": ["8086:8086"],
            "environment": {
                "DOCKER_INFLUXDB_INIT_MODE":         "setup",
                "DOCKER_INFLUXDB_INIT_USERNAME":     "admin",
                "DOCKER_INFLUXDB_INIT_PASSWORD":     admin_password,
                "DOCKER_INFLUXDB_INIT_ORG":          config.influx.org,
                "DOCKER_INFLUXDB_INIT_BUCKET":       config.influx.bucket,
                "DOCKER_INFLUXDB_INIT_ADMIN_TOKEN":  config.influx.token,
            },
            "volumes": ["influxdb_data:/var/lib/influxdb2"],
            "restart": "unless-stopped",
            "healthcheck": {
                "test": ["CMD", "curl", "-f", "http://localhost:8086/health"],
                "interval": "10s",
                "timeout": "5s",
                "retries": 10,
                "start_period": "60s",
            },
        }

    def _mongodb(self, config: TwinConfig) -> dict:
        # Derive the root credentials from the configured URI so a customised
        # DT_MONGO__URI is honoured instead of a hardcoded literal.
        from urllib.parse import urlsplit

        parts = urlsplit(config.mongo.uri)
        user = parts.username or "admin"
        password = parts.password or "password"
        return {
            "image": "mongo:7",
            "ports": ["27017:27017"],
            "environment": {
                "MONGO_INITDB_ROOT_USERNAME": user,
                "MONGO_INITDB_ROOT_PASSWORD": password,
            },
            "volumes": ["mongodb_data:/data/db"],
            "restart": "unless-stopped",
        }

    def _redis(self) -> dict:
        return {
            "image": "redis:7-alpine",
            "ports": ["6379:6379"],
            "volumes": ["redis_data:/data"],
            "restart": "unless-stopped",
        }

    def _minio(self, config: TwinConfig) -> dict:
        # Console on 9002 — avoids conflict with Mosquitto's WebSocket port (9001).
        return {
            "image": "minio/minio:latest",
            "ports": ["9000:9000", "9002:9001"],
            "environment": {
                "MINIO_ROOT_USER":     config.minio.access_key,
                "MINIO_ROOT_PASSWORD": config.minio.secret_key,
            },
            "command": "server /data --console-address ':9001'",
            "volumes": ["minio_data:/data"],
            "restart": "unless-stopped",
        }

    def _ditto_stack(self, config: TwinConfig) -> dict:
        """
        Eclipse Ditto 3.x service definitions.

        Three things are required for a working local stack:

        1. ditto-cluster DNS alias  — all three Pekko nodes must be discoverable
           under the same DNS name so cluster bootstrap can find peers.

        2. ENABLE_PRE_AUTHENTICATION=true  — tells the gateway to trust the
           x-ditto-pre-authenticated header forwarded by the nginx proxy.

        3. ditto-nginx  — validates basic-auth credentials (ditto:ditto by
           default) and sets the x-ditto-pre-authenticated header.  The gateway
           itself is not published to the host; all traffic flows through nginx.
        """
        _alias = {"default": {"aliases": ["ditto-cluster"]}}
        return {
            "ditto-mongodb": {
                "image": "mongo:5",
                "volumes": ["ditto_data:/data/db"],
                "restart": "unless-stopped",
            },
            "ditto-policies": {
                "image": "docker.io/eclipse/ditto-policies:latest",
                "networks": _alias,
                "environment": {"MONGO_DB_URI": "mongodb://ditto-mongodb:27017/policies"},
                "depends_on": ["ditto-mongodb"],
                "restart": "on-failure",
            },
            "ditto-things": {
                "image": "docker.io/eclipse/ditto-things:latest",
                "networks": _alias,
                "environment": {"MONGO_DB_URI": "mongodb://ditto-mongodb:27017/things"},
                "depends_on": ["ditto-mongodb"],
                "restart": "on-failure",
            },
            "ditto-gateway": {
                "image": "docker.io/eclipse/ditto-gateway:latest",
                "networks": _alias,
                # Not published — nginx is the only host-facing entry point.
                "environment": {
                    # Trust the x-ditto-pre-authenticated header from nginx.
                    "ENABLE_PRE_AUTHENTICATION": "true",
                },
                "depends_on": ["ditto-policies", "ditto-things"],
                "restart": "on-failure",
            },
            "ditto-nginx": {
                "image": "nginx:alpine",
                "ports": ["8080:8080"],
                "configs": [
                    {"source": "ditto_nginx_conf",     "target": "/etc/nginx/nginx.conf"},
                    {"source": "ditto_nginx_htpasswd", "target": "/etc/nginx/nginx.htpasswd"},
                ],
                "depends_on": ["ditto-gateway"],
                "restart": "unless-stopped",
            },
        }

    def _ditto_configs(self, config: TwinConfig) -> dict:
        """Inline Docker Compose configs for the Ditto nginx proxy."""
        user     = config.ditto.user
        password = config.ditto.password
        htpasswd_line = f"{user}:{_apr1_hash(password)}"

        nginx_conf = (
            "worker_processes 1;\n"
            "events { worker_connections 1024; }\n"
            "http {\n"
            "  upstream ditto-gateway { server ditto-gateway:8080; }\n"
            "  server {\n"
            "    listen 8080;\n"
            "    # Health probe — no auth required\n"
            "    location /health {\n"
            "      proxy_pass       http://ditto-gateway;\n"
            "      proxy_set_header Host $$host;\n"
            "    }\n"
            "    # Everything else — validate basic auth, then pre-authenticate\n"
            "    location / {\n"
            "      auth_basic           \"DITTO\";\n"
            "      auth_basic_user_file /etc/nginx/nginx.htpasswd;\n"
            "      proxy_pass           http://ditto-gateway;\n"
            "      proxy_set_header     Host $$host;\n"
            "      proxy_set_header     x-ditto-pre-authenticated \"nginx:$$remote_user\";\n"
            "      proxy_set_header     X-Real-IP        $$remote_addr;\n"
            "      proxy_set_header     X-Forwarded-For  $$proxy_add_x_forwarded_for;\n"
            "    }\n"
            "  }\n"
            "}\n"
        )

        # Escape any remaining $ in the htpasswd hash (APR1 format: $apr1$salt$hash)
        # so Docker Compose does not treat them as variable references.
        htpasswd_content = (htpasswd_line + "\n").replace("$", "$$")

        return {
            "ditto_nginx_conf": {
                "content": nginx_conf,
            },
            "ditto_nginx_htpasswd": {
                "content": htpasswd_content,
            },
        }

    def _neo4j(self, config: TwinConfig) -> dict:
        return {
            "image": "neo4j:5",
            "ports": ["7474:7474", "7687:7687"],
            "environment": {
                "NEO4J_AUTH": f"{config.neo4j.user}/{config.neo4j.password}",
            },
            "volumes": ["neo4j_data:/data"],
            "restart": "unless-stopped",
        }

    def _grafana(self) -> dict:
        return {
            "image": "grafana/grafana:latest",
            "ports": ["3000:3000"],
            "volumes": ["grafana_data:/var/lib/grafana"],
            "restart": "unless-stopped",
        }
