# SmallCloud Task Board

**Format:** Now / Next / Later  
**Last updated:** 2026-10-13  
**Rule:** Only M0 and onboarding tasks live in **Now**. When Now is empty, pull from Next. If a new idea comes up that isn't in the current milestone, put it in **Later** — do not start it.

---

## 🔴 Now — actively being worked on (M0 only)

| ID | Task | Owner | Status | Notes |
|---|---|---|---|---|
| S1 | Set up the repo and branch protection | — | ✅ Done | Repo created, `.gitignore`, `pyproject.toml` |
| S2 | Write the ADR for every decision already made | — | ✅ Done | `docs/adr.md` decisions recorded |
| S3 | Add CI (GitHub Actions: ruff + pytest, skip Docker tests) | — | ✅ Done | `.github/workflows/ci.yml` |
| S4 | Write working files (CONTRIBUTING, PR template, CODEOWNERS) | — | ✅ Done | All three files created |
| T0.5 | Spike — confirm Docker resource limits work locally | — | ✅ Done | Results in PRD section 17 |
| T0.6 | Spike — measure idle memory (trivial app + Streamlit) | — | ✅ Done | 14.6 MiB / 99.7 MiB |
| T0.7 | Write the threat model | — | ✅ Done | `docs/threat_model.md` — 8 threats, 16 planned tests |
| T0.8 | Create the task board | — | ✅ Done | This file |

> **M0 is complete when all rows above are ✅ Done and a teammate has reviewed the threat model and ADR.**

---

## 🟡 Next — M1: Deploy and run (ready to start after M0 is done)

Goal: paste code, get a working link (no login required yet). Target: weeks 1–2.

| ID | Task | Owner | Depends on | Notes |
|---|---|---|---|---|
| M1.1 | Data model — create SQLAlchemy models and Alembic migration | — | M0 done | Tables: `users`, `apps`, `deployments` |
| M1.2 | Deploy endpoint — accept `.py` or `.html` file, detect type, create app record | — | M1.1 | POST `/api/deploy` |
| M1.3 | Docker runtime — implement `start`, `stop`, `logs`, `health`, `delete` | — | M1.1 | Uses existing `DockerRuntime` scaffold |
| M1.4 | Static runtime — serve a single `.html` file at `<slug>.localhost` | — | M1.1 | Uses existing `StaticRuntime` scaffold |
| M1.5 | Proxy — wildcard hostname routing, forward HTTP to the correct container | — | M1.3, M1.4 | `<slug>.apps.localhost` → correct runtime |
| M1.6 | Dashboard (minimal) — list of apps with status and link | — | M1.2 | Jinja2 + HTMX, no auth yet |
| M1.7 | End-to-end test — paste a Python script, get a link, open it | — | M1.3–M1.6 | Manual first, then automated |
| M1.8 | End-to-end test — paste an HTML file, get a link, open it | — | M1.4–M1.6 | Manual first, then automated |

---

## 🔵 Next — M2: Share securely (starts after M1)

Goal: magic-link login, per-app access list, all 16 security tests pass. Target: weeks 3–4.

| ID | Task | Owner | Notes |
|---|---|---|---|
| M2.1 | Magic-link auth — generate, email (console), redeem, create session | — | Single-use, 15-min expiry, hashed |
| M2.2 | Per-app access list — add/remove emails, check on every request in proxy | — | `app_access` table |
| M2.3 | Security tests — write all 16 tests from `docs/threat_model.md` | — | See threat model traceability matrix |
| M2.4 | Header spoofing test passes (`test_header_spoofing_blocked`) | — | T1 |
| M2.5 | Uninvited access test passes (`test_uninvited_user_gets_403`) | — | T8 |
| M2.6 | Magic-link tests pass (single-use, expiry, hashed) | — | T7 |
| M2.7 | Container security tests pass (non-root, read-only, limits) | — | T3, T4 |

---

## ⚪ Later — M3 and beyond (do not start until Next is empty)

### M3: Feels like a product (weeks 5–7)

| ID | Task | Notes |
|---|---|---|
| M3.1 | Sleep/wake — idle stop after 5 min, wake on first request | Loading page covers wake time |
| M3.2 | Storage — `/data` volume for Python, key-value API for HTML apps | |
| M3.3 | Secrets — encrypted at rest, injected as env vars, never returned by API | |
| M3.4 | Live logs — SSE streaming, last 1,000 lines retained | |
| M3.5 | Full dashboard — redeploy, pause, delete, expiry, access list UI | |
| M3.6 | User testing — 3 to 5 people try the prototype | Observe; ask what they would pay |
| M3.7 | ZIP upload (multi-file projects) | US-11 — reject unsafe paths, > 500 files, > 50 MB |
| M3.8 | Measure real-app memory — 5 AI-generated apps | Adjust limits if needed |

### M4: Agent-ready (week 8)

| ID | Task | Notes |
|---|---|---|
| M4.1 | API keys — per-user, scoped, revocable | `api_keys` table |
| M4.2 | MCP server — `deploy` and `share` tools | |
| M4.3 | CLI — `smallcloud deploy <file>` | `typer`-based |

### M5: Safe for outsiders (weeks 9–10)

| ID | Task | Notes |
|---|---|---|
| M5.1 | gVisor on Linux — add to Docker runtime, test escape prevention | Required before public sign-up |
| M5.2 | Egress restrictions — outbound network limits per container | |
| M5.3 | Quotas — per-owner CPU/memory/app-count limits | |
| M5.4 | Nightly backups — database and `/data` volumes | |

### Post-M5 (out of scope for v1)

| Idea | Why it's Later |
|---|---|
| Billing and pricing pages | Need M3 usage data first |
| Custom domains | Complex DNS; not needed for v1 |
| GitHub import | Non-technical users don't use GitHub |
| Teams and org roles | Single-owner model is sufficient for v1 |
| React/Vite build runner | Only if M3 testers actually bring React projects |
| Multi-region hosting | After product-market fit |

---

## How to use this board

1. **Start of each work session:** Look at Now. Pick one task. Finish it before starting another.
2. **When Now is empty:** Move one item from Next into Now. Tell your teammate in Slack/Discord.
3. **New idea comes up:** Add it to Later with a one-sentence note. Do not start it.
4. **Milestone complete:** Move the milestone row to a "Completed milestones" section (add below), then pull Next → Now.
5. **Update this file** in the same PR as the work it describes.
