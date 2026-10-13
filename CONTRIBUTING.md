# Contributing to SmallCloud

Thank you for contributing! This guide removes guesswork about how we collaborate.

---

## Table of Contents

1. [Branch Names](#branch-names)
2. [Commit Messages](#commit-messages)
3. [Pull Requests](#pull-requests)
4. [Code Review](#code-review)
5. [Running Tests Locally](#running-tests-locally)
6. [Architecture Decision Records (ADRs)](#architecture-decision-records-adrs)

---

## Branch Names

Use the format: `<type>/<task-id>-<short-description>`

| Type | When to use |
|---|---|
| `feat/` | New feature or user-facing capability |
| `fix/` | Bug fix |
| `chore/` | Tooling, CI, dependencies, non-functional |
| `docs/` | Documentation only |
| `refactor/` | Code restructuring without behavior change |
| `test/` | Adding or fixing tests only |

**Examples:**
```
feat/S5-add-file-upload
fix/S7-runtime-timeout
chore/S3-add-ci
docs/S4-contributing-guide
```

> Keep branch names lowercase, use hyphens (no underscores), and include the task ID so it links back to the backlog.

---

## Commit Messages

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <short summary in present tense>

[optional body — explain *why*, not *what*]

[optional footer — e.g. Closes #12, Breaking change]
```

**Examples:**
```
feat(runtime): add static runtime file serving
fix(auth): correct JWT expiry calculation
chore(ci): add ruff lint step to PR workflow
```

---

## Pull Requests

### Keep PRs Small

- **Target ≤ 400 lines changed** per PR (excluding generated files / lock files).
- One logical change per PR — split unrelated work into separate branches.
- If a PR grows beyond 600 lines, flag it in `#dev` and consider splitting.

### PR Checklist

Fill in the PR template (`.github/pull_request_template.md`) before requesting review. The CI must be green before requesting review.

### Merging

- **Squash-merge** into `main` to keep history linear.
- Delete the feature branch after merge.
- Never force-push to `main`.

---

## Code Review

| Role | Expectation |
|---|---|
| **Author** | Self-review the diff before requesting review. Keep PRs small. Respond to comments within 24 hours. |
| **Reviewer** | Review and leave feedback within **24 hours** of being assigned. Approve, request changes, or escalate if blocked. |

### Review Etiquette

- Comments are about the code, not the author.
- Prefix non-blocking suggestions with `nit:` (e.g., `nit: could rename this to be clearer`).
- Ask questions rather than making demands: "What do you think about…?" instead of "Change this to…".
- If you resolve a thread you opened, say so explicitly.

### Who Reviews What

See [CODEOWNERS](.github/CODEOWNERS) for automatic reviewer assignment. You are still welcome to tag anyone for a second opinion.

---

## Running Tests Locally

```bash
# Install all dependencies (once)
uv sync --group dev --group test

# Run the full non-Docker test suite
uv run pytest -v -m "not docker"

# Run with coverage report
uv run pytest -v -m "not docker" --cov=app --cov-report=term-missing

# Run Docker-dependent tests (requires Docker daemon)
uv run pytest -v -m "docker"

# Lint
uv run ruff check .
uv run ruff format --check .

# Auto-fix lint issues
uv run ruff check --fix .
uv run ruff format .
```

> **Docker tests** are decorated with `@pytest.mark.docker` and are skipped in CI. Run them locally when you change anything under `app/runtime/docker_runtime.py`.

---

## Architecture Decision Records (ADRs)

When your PR introduces a significant design decision — a new dependency, a change in data model shape, a choice of protocol — document it in `docs/adr.md`.

**A decision is significant if** a future reader might wonder "why did they do it this way?" and the answer isn't obvious from the code.

**ADR format (append to `docs/adr.md`):**

```markdown
## ADR-<N>: <Title>

**Date:** YYYY-MM-DD  
**Status:** Accepted  
**Context:** What problem are we solving?  
**Decision:** What did we decide?  
**Consequences:** What are the trade-offs?
```

Reference the ADR number in your PR description (e.g., `ADR-3`).
