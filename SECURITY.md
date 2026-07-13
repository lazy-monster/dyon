# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| 0.10.x  | ✅ |
| < 0.10  | ❌ |

Security fixes land on the latest minor release. Older versions are not patched.

## Reporting a vulnerability

Email **galisamuel97@gmail.com** with a description, reproduction steps, and the
affected version. Please do not open a public issue for security reports. You can
expect an acknowledgement within a few working days.

## Deployment hardening checklist

Dyon ships a zero-config **dev mode** that is deliberately open (default
credentials, open CORS, no API key) so a local twin runs with no setup. That
posture is safe only on a trusted lab network. Before exposing a twin beyond
localhost, work through this list — it mirrors the checks in
`dyon.core.security.find_insecure_settings`, which `mode="production"` enforces
at startup:

- **Set every credential.** Replace the factory defaults via `DT_*` environment
  variables:
  - `DT_INFLUX__TOKEN`
  - `DT_MONGO__URI` (with real user:password)
  - `DT_MINIO__ACCESS_KEY` / `DT_MINIO__SECRET_KEY`
  - `DT_DITTO__PASSWORD`
  - `DT_NEO4J__PASSWORD`
- **Turn on production mode:** `DT_SECURITY__MODE=production`. The twin then
  refuses to start while any insecure default remains.
- **Set an API key:** `DT_SECURITY__API_KEY=<a long random string>`. Every
  `/api/*` HTTP and WebSocket route then requires it (via the `x-api-key` header
  or an `api_key` query parameter for EventSource/WebSocket clients). `/health`
  and the static dashboard HTML stay public; the data behind them does not.
- **List allowed CORS origins:** `DT_SECURITY__CORS_ORIGINS='["https://dash.example.com"]'`.
  In production mode CORS answers only these origins (never `*`).
- **Keep `api_host` bound to localhost** unless you intend to expose the port.
  The default is `127.0.0.1`; set `DT_API_HOST=0.0.0.0` explicitly (and only with
  an API key set) to listen on all interfaces.
- **Enable MQTT TLS** when the broker is remote: `DT_MQTT__TLS=true` (typically
  with `DT_MQTT__PORT=8883`), and set `DT_MQTT__USERNAME` / `DT_MQTT__PASSWORD`.
- **Front the HTTP API with HTTPS.** The framework does not terminate TLS for its
  own HTTP server; run it behind a reverse proxy (nginx, Caddy, a cloud LB) that
  does.

## Artifact integrity

Trained policies, reward nets, and demonstration datasets are pickle-format
files (SB3/torch). Only load artifacts obtained through
`dyon.ml.corpus.TrainingCorpus.download_version`, which verifies each file
against the SHA-256 checksum recorded in the manifest before returning it. Never
point `PolicyDeployer` or `LearnedRewardFn.load` at an untrusted file.
