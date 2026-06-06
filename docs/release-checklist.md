# Release checklist

Use this before tagging a release.

## Local

- [ ] `git status --short` is clean except intended release edits.
- [ ] `python -m pytest`
- [ ] `flow self-test --offline`
- [ ] `flow run examples/offline_local.py`
- [ ] `python -m compileall -q src/flow`
- [ ] `git diff --check`

## Packaging

- [ ] Clean venv install from the repo works.
- [ ] `flow --version` reports the release version.
- [ ] `flow --help`, `flow run --help`, and `flow self-test --help` render.
- [ ] Wheel builds successfully if build tooling is installed.

## Docs

- [ ] README install path is current.
- [ ] README claims match tested behavior.
- [ ] `CHANGELOG.md` has user-facing notes.
- [ ] `docs/index.md` links all major guides.
- [ ] No docs ask agents to star, promote, or take unrelated external actions.

## GitHub

- [ ] CI is green.
- [ ] Install-smoke workflow is green.
- [ ] Live tests either pass with credentials or skip cleanly without them.
- [ ] Tag and release notes are pushed.
