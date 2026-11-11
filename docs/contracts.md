# System Contracts: Runtime & Proxy

This document defines the interface boundary between application runtimes, the deploy endpoint, schedulers, and the reverse proxy.

## 1. Runtime Contract (`app.runtime.base.Runtime`)

All container and execution backends (Docker, gVisor, in-memory test fakes) implement the synchronous `Runtime` protocol:

* `start(slug, version_dir, kind, env) -> StartResult(host_port, instance_id)`
* `pause(slug) -> None`
* `unpause(slug) -> None`
* `stop(slug) -> None`
* `delete(slug) -> None`
* `status(slug) -> RuntimeState` (`"running"`, `"paused"`, `"stopped"`, `"missing"`)
* `logs(slug, tail=200) -> str`
* `wait_until_ready(slug, timeout=30.0) -> None`

### Runtime Rules
- **Fixed Internal Port:** Every app container listens exclusively on port `8080` (`INTERNAL_PORT = 8080`). The runtime maps this to a dynamic host port on `127.0.0.1`. Static apps return `StartResult(None, None)`.
- **Idempotence:** `stop` and `delete` are idempotent. Calling them on already stopped or missing instances succeeds cleanly.
- **State Guards:** `start` on an already running slug raises `RuntimeFailure`. `pause` requires state `"running"`; `unpause` requires `"paused"`.
- **Unknown Slugs:** `status` for an unknown slug returns `"missing"`.
- **No Database Writes in Runtime:** A runtime implementation NEVER reads or writes the database. State persistence is owned by callers.
- **Concurrency:** Methods are synchronous; async callers (FastAPI routes, reverse proxy) must invoke them via `asyncio.to_thread`.

## 2. Failure Handling (`RuntimeFailure`)

Any runtime failure raises `RuntimeFailure` with keyword-only diagnostic fields:
- `log_tail: str = ""` (trailing log output from the container)
- `exit_code: int | None = None`
- `oom_killed: bool = False`
- `timed_out_waiting_for_port: bool = False`

Calling `failure.classify(kind=..., memory_limit_mib=...)` delegates to `app.friendly_errors.classify_failure` to produce a user-facing `FriendlyError`. `str(failure)` is always a fixed sentence, never raw log text.

## 3. Database Ownership & Route Lookup (for Proxy & Schedulers)

Only the deploy endpoint and schedulers write app state to the database, exclusively via `app.route_lookup.change_status`.

For the reverse proxy author:
1. `lookup_route(slug)` is synchronous: call it via `asyncio.to_thread` from async proxy request handlers.
2. `change_status(slug, status, host_port=...)` saves state and port atomically; `"running"` requires an explicit port.
3. `route.upstream_url` provides the target URL (`f"http://127.0.0.1:{host_port}"`) where the proxy must forward incoming visitor HTTP traffic.

---
Status: proposed by the founder, to be approved by the runtime owner in the pull request.
