# Changelog

All notable changes to Dyon are documented here. This project adheres to
[Semantic Versioning](https://semver.org/).

## [0.11.0] — 2026-08-18

### Added

- **A twin can now run with no infrastructure at all.** Every storage protocol
  has an in-process implementation alongside its networked one:
  `InMemoryTimeSeriesAdapter`, `InMemoryDocumentAdapter`, `InMemoryCacheAdapter`,
  `InMemoryObjectAdapter`, and `FileBackedObjectAdapter`, all exported from
  `dyon.data`. The cache exposes a redis-shaped client, so `SessionStore` keeps
  its expiry and listing behaviour rather than silently losing them.
- **Cross-twin state exchange without a broker.** `InProcessDittoClient` and
  `ThingRegistry` (`dyon.services.ditto`) implement the `DittoClient` contract
  against a registry of Things held in memory, and `InProcessConnector`
  (`dyon.connector`) does the same for the collection layer's boundary exchange.
  A composite whose members share a process no longer needs Eclipse Ditto to
  move state between two objects in the same interpreter.
- **A reasoning tier that constructs without a provider.** `OfflineChatModel`
  (`dyon.intelligent`) is a real `BaseChatModel` that answers in-process and
  accepts tool binding, so `create_tool_calling_agent` builds against it and an
  agent graph compiles identically online or off. Reach it with
  `DT_LLM__PROVIDER=offline`, and pass a `responder` to give it a domain voice.
- **`NullGraphDriver`** (`dyon.intelligent`) satisfies the knowledge-graph driver
  contract without Neo4j, so schema setup is a no-op and queries return empty
  instead of timing out one statement at a time. Threshold-driven symptom
  detection is unaffected, since it evaluates the spec in Python.
- **`ModelStateFeed`** (`dyon.simulation`) for twins whose model *is* the state
  of record rather than something to compare readings against. It steps its
  models and routes their state through the twin's ordinary telemetry path, so
  alarms, Ditto sync, and dashboards work unchanged. Use it instead of
  `ModelRunner` for the same models, never alongside.

Everything here is additive and opt-in. A twin that asks for none of it behaves
exactly as it did.

[0.11.0]: https://pypi.org/project/dyon/0.11.0/

## [0.10.2] — 2026-07-13

### Changed

- The developer guide now presents the architecture as four layers — Data,
  Simulation and Model, Services, and Agent — over two enabling layers, with the
  Agent layer's control, reasoning, and governance tiers taking a chapter each.
  This is a documentation change only: those tiers remain the `dyon.reactive`,
  `dyon.intelligent`, and `dyon.autonomous` modules, and no API changed.
- Docstrings, examples, and tests throughout now illustrate the framework with
  plain industrial assets. A domain-agnostic framework is better served by
  generic examples.

### Fixed

- The guide named two helpers that the package does not ship (a Mongo-backed
  demonstration source and an auto-sync entry point). It now documents what is
  actually provided: subclassing `DemonstrationSource`, and driving `SyncTrigger`
  from your own background task.
- The guide pointed readers at example directories that were never distributed.
- Chapter 13 ended with an unterminated code fence.

[0.10.2]: https://pypi.org/project/dyon/0.10.2/

## [0.10.1] — 2026-07-13

### Fixed

- **The dashboard could not be served from a clean install.**
  `mount_visualization()` binds a speech-to-text route that accepts an
  `UploadFile`, and FastAPI refuses to build a form route unless
  `python-multipart` is present. It was not declared as a dependency, so
  `pip install dyon` followed by any attempt to serve the dashboard — directly,
  through `create_app(include_viz=True)`, or via `dyon dashboard` — raised
  `RuntimeError: Form data requires "python-multipart" to be installed`. It is
  now a core dependency, matching the documented promise that a plain install
  includes the dashboard. Anyone on 0.10.0 who hit this should upgrade.
- `make_space_venv()` passed `Space.shape` straight to `np.zeros`, which fails
  for spaces that report no shape (`Text`, `Graph`). The stub observation now
  falls back to a scalar array.

### Changed

- The `ruff` and `mypy` floors in the `dev` extra, and the `ruff` revision used
  by `pre-commit`, now track the versions in `uv.lock` that CI resolves from.
  They had drifted far enough behind that a local run could report a clean tree
  while CI failed on rules the older tools did not implement.

[0.10.1]: https://pypi.org/project/dyon/0.10.1/

## [0.10.0] — 2026-07-03

Production-hardening release: close the security and operational gaps that made
0.9 safe only on a trusted lab network. See `SECURITY.md` for the deployment
hardening checklist.

### Added

- **Security posture (`SecurityConfig`).** `DT_SECURITY__MODE=production` refuses
  to start when any factory-default credential, wildcard CORS, or unset API key
  is present (`dyon.core.security.assert_production_safe`). Dev mode is unchanged.
- **API-key authentication.** A pure-ASGI middleware guards every HTTP *and*
  WebSocket route when `DT_SECURITY__API_KEY` is set — via an `x-api-key` header
  or an `api_key` query parameter (for EventSource/WebSocket clients). `/health`
  and the static dashboard stay public; the data behind them does not. The
  dashboard client sends the key automatically.
- **Artifact integrity.** `TrainingCorpus` records a SHA-256 per version and
  verifies every download against it before deserialization, raising
  `IntegrityError` on mismatch. Demonstration archives load with
  `allow_pickle=False`.
- **LLM guardrails.** Every client carries a timeout, token cap, and retries; the
  overseer rejects any action outside `available_actions`; untrusted telemetry,
  events, and tool output are fenced as `<data>…</data>` against prompt injection.
- **MQTT TLS** (`DT_MQTT__TLS`), reconnect backoff, and initial-connect retry.
- **Health counters.** A tiny `dyon.core.metrics` registry surfaces dropped
  writes and other failures on `/health`; storage/event-bus write failures now
  increment a counter and log at WARNING instead of failing silently.
- **Typed distribution.** Ships `py.typed`; `mypy dyon/` passes; ruff runs an
  expanded rule set; CI (GitHub Actions) runs lint + types + tests on 3.11/3.12;
  `.pre-commit-config.yaml` added.
- **Tests.** 15 new unit-test files covering the above and previously untested
  subpackages; a coverage gate in the default `pytest` run.

### Changed

- **Breaking:** `api_host` now defaults to `127.0.0.1` (was `0.0.0.0`). Set
  `DT_API_HOST=0.0.0.0` explicitly — with an API key — to expose beyond localhost.
- **Breaking:** heavy dependencies moved to optional extras. `pip install dyon`
  is now a lean MQTT→API core; use `pip install 'dyon[all]'` to restore 0.9
  behaviour, or pick extras (`stores`, `agents`, `rl`, `sim`, `forecast`,
  `voice`, `hardware`). Missing extras raise an actionable install hint.
- **Breaking:** `prophet` is no longer a core dependency (moved to the `forecast`
  extra).
- CORS is configurable and never unconditionally `*` in production mode.
- Error responses no longer contain exception text (chat, viz routers, and the
  core twin routes return generic messages and log the detail server-side).

### Fixed

- Ditto HTTP client is closed on shutdown (was leaked every stop).
- `TelemetryRouter` drains its queue on shutdown with a deadline instead of
  discarding everything after the first bad item.
- Unbounded `TextIngestor` queue is now bounded (backpressure).
- Connectors reuse one pooled `httpx` client instead of a handshake per call.
- `EventBus` awaits in-flight handlers on shutdown; corrupt session records are
  logged and skipped; webhook URLs are redacted to host-only in logs.

[0.10.0]: https://pypi.org/project/dyon/0.10.0/

## [0.9.0] — 2026-06-29

### Added

- **Combined / federated dashboards.** `derive_combined_spec` and
  `combined_spec_from_twin` build a federated `DashboardSpec` for an aggregate,
  collection, composite, or network twin. The client renders a type-specific
  overview — fleet roll-up, peer ranking, hierarchy-and-flow diagram, or
  relationship graph — and drills into each member's own full dashboard,
  federated from its `api_base`. Serve members store-lessly over CORS with
  `create_combined_dashboard_app`, or bundle several twins in one process under a
  per-member `base_path`.
- **Agents tab.** `GET /api/viz/agents` surfaces each agent's latest
  observe→reason→act snapshot and tool calls from the `MultiAgentSystem`; the
  client renders a live card per agent. Generic to any twin with a MAS, and shown
  for a composite overseer in its combined overview too.
- **Ready-made dashboard chat agent.** `make_dashboard_chat_agent(...)` builds a
  `DashboardChatAgent` — a diagnostic agent already equipped with the chart and
  forecast tools — so the "Ask the Twin" panel answers in prose *and* draws charts
  with no manual tool wiring. `create_app(..., chat_agent=)` threads it onto the
  single `/api/chat` route (mounted exactly once, whether or not the core chat is
  enabled), as does `mount_visualization(chat_agent=)`.
- **Live 3D condition cues.** A scene's `stress_field` drives a colour wash over
  the model (healthy → amber → brown) from that field's warn/crit bounds, and
  `stage_models` swaps the model for a per-level GLB — so the asset's *condition*,
  not just its readings, shows on the model.

### Changed

- The dashboard client is now a tabbed **app shell** — a sidebar, a persisted
  light/dark theme, and a per-twin view split into **Home** (the asset's 3D scene,
  a live agent-chart canvas, and chat), **Telemetry** (KPIs, charts, alarms,
  events), and **Agents** — replacing 0.8.0's single-page layout. Panels route
  into regions by `kind`, so any spec still lays out sensibly.

### Fixed

- `build_llm` dropped the configured API key for the Ollama provider, so Ollama
  Cloud requests went out unauthenticated and an ambient `OLLAMA_API_KEY` shell
  variable could silently win. The key now rides in as a lowercase
  `authorization: Bearer` header forwarded to both the sync and async clients.
- The forecast endpoint and agent tool pointed users at the empty `dyon[viz]`
  extra when the backend was missing; they now name the correct `dyon[forecast]`
  extra.

[0.9.0]: https://pypi.org/project/dyon/0.9.0/

## [0.8.0] — 2026-06-26

### Added

- **Visualization module (`dyon.visualization`)** — an opt-in, in-browser
  dashboard built from a twin's existing config. Turn it on with
  `create_app(..., include_viz=True)` or `mount_visualization(app, …)`; it serves
  KPI tiles, unit-grouped trend charts, a threshold-driven alarm banner, an event
  log, and a conversational panel under `/api/viz/*`, plus the static client at
  `/dashboard`. Live updates stream the twin's events to the browser; the chat
  panel reaches the diagnostic agent and can answer with charts (via
  `make_chart_tool` / `make_forecast_tool`) or speech (browser-native by default).
  An optional, compute-gated 3D viewport colours a model from live readings and
  falls back to a 2D schematic where WebGL is unavailable. Fully additive — a twin
  that does not opt in is unchanged, and a regression test asserts the default
  `create_app()` exposes no `/api/viz/*` routes.
- `dyon dashboard` CLI command to serve the dashboard against a running twin.
- Optional extras `dyon[forecast]` (forecasting backend) and `dyon[voice]`
  (server-side speech). The dashboard itself needs neither.
- Developer guide chapter 13 (Visualization).

### Fixed

- `dyon.__version__` was stuck at `0.6.0` while the packaged version had moved on;
  it now tracks the release version again.

[0.8.0]: https://pypi.org/project/dyon/0.8.0/

## [0.7.1] — 2026-06-21

### Changed

- **Relicensed under the PolyForm Noncommercial License 1.0.0**, replacing the
  MIT label carried by 0.7.0 and earlier. Noncommercial use (personal, academic,
  nonprofit, government) remains free; commercial use now requires a separate
  commercial license (contact galisamuel97@gmail.com). Relicensing binds new
  releases only and does not change the terms on which any earlier release was
  received. Releases predating 0.7.1 have since been withdrawn from PyPI.
- Added package metadata for publication — authors, keywords, trove classifiers,
  and project URLs — on both `dyon` and the `dt-forge` redirect distribution.

## [0.7.0] — 2026-06-20

### Changed

- **Renamed `dt-forge` → `dyon`.** The package is now `dyon` (import `dyon`) and
  the command-line tool is `dyon`. The change is backwards compatible: installing
  the `dt-forge` redirect distribution (which depends on `dyon`) provides a
  `dt_forge` compatibility shim that transparently redirects every old import to
  its `dyon` equivalent, preserving module identity; the legacy `dtforge` command
  also remains available. Both paths emit a one-time `DeprecationWarning` and will
  be removed in a future major release. Migrate by replacing `dt_forge` with
  `dyon` in imports and `dtforge` with `dyon` on the command line.

## [0.6.0] — 2026-06-12

Hardening release following a full codebase assessment. No breaking API changes;
the one renamed symbol keeps a backwards-compatible alias.

### Fixed

**Critical**
- **KnowledgeGraph wiring** — the constructor now rejects a `KnowledgeGraphSpec`
  or any non-driver argument (raises `TypeError`) instead of silently disabling
  diagnostics. Implementations build a real Neo4j driver and call
  `setup_from_spec`.
- **Graceful shutdown** — `TwinLifecycle.run_forever` now stops layers before
  cancelling the run task and awaits the cancellation; `AbstractDigitalTwin`
  supervises layer tasks so a failing layer cancels its siblings and surfaces the
  error, with bounded shutdown time.
- **Provenance chain** — `verify_chain` anchors on the oldest *retained* entry, so
  it survives capped-collection eviction while still detecting tampering.

**Blocking I/O on the event loop (§3)**
- Storage adapters gained async `a*` methods (thread-offloaded) and batched reads;
  every continuous loop (telemetry, both rule engines, model runner, PID, OODA,
  Ditto sync, MAS) now awaits them.
- `LogEventAction.execute` no longer calls the blocking `log_event` on the loop —
  it awaits `alog_event`.
- `DataManagementPipeline.run_once` replaced a blocking per-field query round trip
  with a single thread-offloaded `aquery_recent_fields`.

**Correctness & robustness**
- Single-bound thresholds (only `crit` or only `warn`) are now honoured in config
  and all consumers.
- Missing telemetry is no longer coerced to `0.0` (Ditto sync skips `None`;
  deployer falls back to a nominal value).
- `GenericTwinEnv.reset` primes a real first observation via a neutral model step.
- Config-driven health-check host/port; corpus key de-duplication; Flux identifier
  validation; Influx list-result normalisation and source-timestamp support;
  reused Ditto `httpx.AsyncClient`; NetworkDT baseline-connectivity guard;
  word-boundary sentiment matching.
- **Windows support** — `run_forever` falls back to `signal.signal` when the event
  loop does not implement `add_signal_handler` (Windows proactor loops), instead
  of crashing on startup.

### Changed
- `dt_forge.data.text_ingestor._vader_sentiment` is now the public
  `vader_sentiment`. The private name remains as a backwards-compatible alias.
- Removed unused `motor` and `sse-starlette` dependencies.
- Wired the opt-in `RuleRepository` (hot-reloadable rules) and added a
  shutdown-latch recovery API to the rule engine.

[0.6.0]: https://pypi.org/project/dt-forge/0.6.0/
