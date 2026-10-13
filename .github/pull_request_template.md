## Summary

<!-- One or two sentences describing what this PR does and why. -->

Closes # <!-- task ID, e.g. Closes #12 -->

---

## Type of Change

- [ ] `feat` – New feature
- [ ] `fix` – Bug fix
- [ ] `refactor` – Refactoring (no behavior change)
- [ ] `chore` – Tooling, CI, dependencies
- [ ] `docs` – Documentation only
- [ ] `test` – Tests only

---

## Checklist

### Code Quality
- [ ] CI is green (ruff + pytest pass)
- [ ] No new `# noqa` without a comment explaining why

### Tests
- [ ] New behavior is covered by tests
- [ ] Docker-dependent tests are marked with `@pytest.mark.docker`
- [ ] `uv run pytest -v -m "not docker"` passes locally

### Architecture
- [ ] If this introduces a significant design decision, an ADR has been added to `docs/adr.md`
- [ ] ADR number (if applicable): `ADR-`

### Task Tracking
- [ ] Task ID is referenced in the branch name (e.g. `feat/M1.2-deploy-endpoint`)
- [ ] Task ID is referenced in this description

---

## How to Test This

1. 
2. 

---

## Screenshots / Logs (if applicable)
