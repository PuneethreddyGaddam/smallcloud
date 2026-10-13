# SmallCloud Threat Model

**Version:** 0.1  
**Date:** 2026-10-13  
**Status:** Draft — M0 deliverable  
**Authors:** SmallCloud team  
**Source:** PRD section 11  
**Review required by:** teammate before M1 starts

---

## Purpose

This document translates every threat listed in PRD section 11 into four concrete fields:

| Field | Meaning |
|---|---|
| **Asset** | What is being protected |
| **Attacker** | Who or what could cause harm |
| **Mitigation** | The code, configuration, or policy that prevents the threat |
| **Automated test** | The test function that proves the mitigation works (planned for M2 or M5) |

The test IDs in this document are the authoritative list of security tests for M2 and M5. When you write a test for a threat, use the exact function name listed here so the traceability is clear.

---

## Scope and assumptions

- **v1 runs trusted code only.** The team's own code and code from invited users runs locally. Hostile or publicly uploaded code is explicitly out of scope until milestone M5 (gVisor on Linux).
- **Local Docker is not a strong isolation boundary** on macOS or Windows. The container hardening below provides defence-in-depth, not a sandbox guarantee.
- **gVisor** is required before any public sign-up (M5). Until then, the platform must never be exposed to untrusted code.

---

## Threat table

### T1 — Header spoofing

| Field | Detail |
|---|---|
| **Asset** | The viewer's verified identity (`X-Platform-User` header), which apps use to personalise content and enforce access |
| **Attacker** | A malicious viewer who crafts an HTTP request with a forged `X-Platform-User` header to impersonate another user or claim admin identity |
| **Mitigation** | The reverse proxy (`app/proxy/`) **strips** any incoming `X-Platform-User` header on every request before forwarding it to the app, then **injects** a freshly signed header derived from the authenticated session. Apps can only receive a header that the proxy itself wrote. |
| **Automated test** | `tests/test_proxy_security.py::test_header_spoofing_blocked` — sends a request to the proxy with a forged `X-Platform-User: admin@example.com` header; asserts the app receives a header signed by the platform, not the forged value; also asserts a request with no valid session receives a 401. |
| **Milestone** | M2 |

---

### T2 — Cookie theft between apps

| Field | Detail |
|---|---|
| **Asset** | A user's session cookie for App A. If stolen, an attacker could impersonate that user across all apps. |
| **Attacker** | A malicious app (App B) that tries to read App A's session cookie via JavaScript, a shared `Set-Cookie` domain, or a cross-origin request |
| **Mitigation** | (1) Cookies are `HttpOnly` (no JavaScript access). (2) Each app is served from its own subdomain (`<slug>.apps.localhost`) so the browser's same-origin policy prevents App B from reading App A's cookie. (3) The dashboard is served from a separate origin (`localhost:8000`), not the apps domain. (4) Sessions are stored server-side; the cookie contains only a signed session token. |
| **Automated test** | `tests/test_auth_security.py::test_cookie_not_accessible_cross_app` — verifies that a session cookie issued for `app-a.apps.localhost` does not appear in a request to `app-b.apps.localhost`; also verifies `HttpOnly` flag is set on every `Set-Cookie` response. |
| **Milestone** | M2 |

---

### T3 — App escapes its container

| Field | Detail |
|---|---|
| **Asset** | The host machine's filesystem, processes, network, and other running apps |
| **Attacker** | A malicious or buggy deployed app that attempts a container escape (e.g., via a kernel exploit, mounted Docker socket, or privilege escalation) |
| **Mitigation** | Containers are launched with: non-root user, all Linux capabilities dropped (`--cap-drop=ALL`), read-only root filesystem (`--read-only`), a writable `/tmp` tmpfs, a CPU limit (0.5 cores), a memory limit (512 MiB), a PID limit (100), and no access to the Docker socket. gVisor (M5) adds a second kernel boundary on Linux. |
| **Automated test** | `tests/test_runtime_security.py::test_container_runs_as_nonroot` — starts a test container via the Docker runtime, executes `id` inside it, asserts UID is not 0. `test_container_filesystem_is_readonly` — attempts to write to `/etc/passwd` inside the container; asserts it fails with a permission error. |
| **Milestone** | M2 (capability/UID checks); M5 (gVisor boundary) |

---

### T4 — Resource abuse (miners, fork bombs)

| Field | Detail |
|---|---|
| **Asset** | The host machine's CPU, memory, and process table; also the availability of other users' apps |
| **Attacker** | A deployed app (even a trusted one, through a bug) that consumes unbounded CPU, spawns thousands of processes, or allocates all available memory |
| **Mitigation** | Docker resource limits: `--cpus=0.5`, `--memory=512m`, `--pids-limit=100`. Idle stop after ~5 minutes without requests (frees all memory). Egress restrictions planned for M5. The in-process scheduler forcibly stops containers that exceed their idle timeout regardless of CPU activity. |
| **Automated test** | `tests/test_runtime_security.py::test_container_cpu_limit_applied` — inspects the created container's `HostConfig` and asserts `NanoCpus` equals the configured limit. `test_container_memory_limit_applied` — asserts `Memory` equals 512 MiB. `test_container_pids_limit_applied` — asserts `PidsLimit` equals 100. |
| **Milestone** | M2 (limit assertions); M5 (egress) |

---

### T5 — One app reads another app's data

| Field | Detail |
|---|---|
| **Asset** | The `/data` volume belonging to each deployed app, which may contain sensitive user data |
| **Attacker** | A deployed app (App B) that attempts to read or write App A's `/data` directory, either through a shared mount or through the platform's storage API |
| **Mitigation** | Each app gets its own named Docker volume (`app-<slug>-data`) mounted exclusively to that app's container. No shared mounts exist. The storage API (`app/storage/`) scopes every read/write to the requesting app's ID, validated from the signed `X-Platform-App` header set by the proxy. Apps are on separate Docker networks; they cannot reach each other's ports. |
| **Automated test** | `tests/test_storage_security.py::test_app_cannot_access_other_app_data` — creates two test app records, calls the storage API as App A to write a key, then calls it with App B's credentials and asserts the key is not visible. `test_volumes_are_separate` — inspects the Docker volume mounts of two running containers and asserts they do not share any volume. |
| **Milestone** | M2 |

---

### T6 — Leaked secrets

| Field | Detail |
|---|---|
| **Asset** | Per-app API keys and environment variable secrets stored in the database and injected into containers |
| **Attacker** | (a) A platform bug that returns secret values in API responses or logs them. (b) An attacker with read access to the database who can read plaintext secrets. |
| **Mitigation** | Secrets are encrypted at rest using a platform key (AES-256 via the `cryptography` library). The API endpoints for secrets never return the plaintext value — only key names. The structured logger is configured to redact fields named `secret`, `value`, and `token`. Secrets are injected into containers as environment variables at start time and never written to disk. |
| **Automated test** | `tests/test_secrets_security.py::test_secret_value_not_returned_by_api` — creates a secret via the API, then calls GET on the secrets endpoint and asserts the response body does not contain the plaintext value. `test_secret_encrypted_in_database` — reads the raw database row and asserts the stored value is not equal to the plaintext. |
| **Milestone** | M2 |

---

### T7 — Stolen magic link

| Field | Detail |
|---|---|
| **Asset** | A magic login link sent to a viewer's email. If stolen (e.g., from email logs or a forwarded email), it could grant unauthorised access. |
| **Attacker** | An attacker who intercepts or obtains a magic link before the legitimate user clicks it |
| **Mitigation** | (1) Single-use: the link is invalidated in the database on first use. (2) 15-minute expiry: the `expires_at` column is checked on every use. (3) Hashed storage: only a SHA-256 hash of the token is stored; the plaintext token exists only in the email. (4) Rate limiting: no more than 3 magic links can be sent to the same email within 10 minutes. |
| **Automated test** | `tests/test_auth_security.py::test_magic_link_single_use` — redeems a magic link once (expect 200), redeems it again (expect 401). `test_magic_link_expires` — creates a link with `expires_at` in the past; asserts redemption returns 401. `test_magic_link_token_hashed_in_db` — asserts the raw token string does not appear anywhere in the database after creation. |
| **Milestone** | M2 |

---

### T8 — Access list bypass

| Field | Detail |
|---|---|
| **Asset** | A private app's content, which should only be visible to the Owner and explicitly invited email addresses |
| **Attacker** | A user with a valid session (they have logged in before) who tries to access an app they have not been invited to, by crafting a direct URL |
| **Mitigation** | The proxy (`app/proxy/`) checks the access list on **every request**, not only at login. A valid session is necessary but not sufficient — the user's email must appear in the `app_access` table for that specific app. Removing a user from the access list takes effect on the next request, with no cache delay. |
| **Automated test** | `tests/test_proxy_security.py::test_uninvited_user_gets_403` — creates a session for User B (valid session), requests App A (which only User A owns), asserts 403. `test_access_revocation_immediate` — grants User B access, makes a request (200), revokes access, makes the same request again (403) — all without invalidating the session. |
| **Milestone** | M2 |

---

## Test traceability matrix

| Test function | Threat | Milestone | File |
|---|---|---|---|
| `test_header_spoofing_blocked` | T1 | M2 | `tests/test_proxy_security.py` |
| `test_cookie_not_accessible_cross_app` | T2 | M2 | `tests/test_auth_security.py` |
| `test_container_runs_as_nonroot` | T3 | M2 | `tests/test_runtime_security.py` |
| `test_container_filesystem_is_readonly` | T3 | M2 | `tests/test_runtime_security.py` |
| `test_container_cpu_limit_applied` | T4 | M2 | `tests/test_runtime_security.py` |
| `test_container_memory_limit_applied` | T4 | M2 | `tests/test_runtime_security.py` |
| `test_container_pids_limit_applied` | T4 | M2 | `tests/test_runtime_security.py` |
| `test_app_cannot_access_other_app_data` | T5 | M2 | `tests/test_storage_security.py` |
| `test_volumes_are_separate` | T5 | M2 | `tests/test_storage_security.py` |
| `test_secret_value_not_returned_by_api` | T6 | M2 | `tests/test_secrets_security.py` |
| `test_secret_encrypted_in_database` | T6 | M2 | `tests/test_secrets_security.py` |
| `test_magic_link_single_use` | T7 | M2 | `tests/test_auth_security.py` |
| `test_magic_link_expires` | T7 | M2 | `tests/test_auth_security.py` |
| `test_magic_link_token_hashed_in_db` | T7 | M2 | `tests/test_auth_security.py` |
| `test_uninvited_user_gets_403` | T8 | M2 | `tests/test_proxy_security.py` |
| `test_access_revocation_immediate` | T8 | M2 | `tests/test_proxy_security.py` |

> **Note:** All 16 tests above are planned. None exist yet. Write them during M2 when the proxy, auth, and storage modules are built. Tests marked M5 will be added when gVisor is introduced.

---

## What is NOT covered (and why)

| Out of scope now | Reason |
|---|---|
| Hostile/untrusted code execution | Trusted code only until M5 (gVisor on Linux) |
| DDoS / volumetric attack | Not addressable at the local single-machine level |
| Email provider compromise | Outside the platform's control; mitigated by short link expiry |
| Supply-chain attacks on dependencies | Addressed by `uv.lock` pinning and future Dependabot setup |
| HTTPS / TLS | Local dev uses HTTP; TLS required before production (planned for post-M5) |
