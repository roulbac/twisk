# CLAUDE.md

twisk is a k9s-style terminal UI for SkyPilot (Textual + the SkyPilot SDK in API-server mode).

## Commands

```bash
uv sync                      # install
uv run twisk --demo          # TUI against the in-memory FakeBackend
uv run twisk sandbox up      # k3s + SkyPilot API server in Docker, then `uv run twisk`
uv run pytest                # unit + Textual pilot tests (~1.5 min)
uv run pytest -m sandbox     # live tests against a running sandbox (~2-3 min, needs ~2 free CPUs)
uv run twisk templates list  # form templates (built-in + ~/.twisk/templates); `init` copies the built-ins
uv sync --group docs && uv run python scripts/capture_screenshots.py   # README screenshots + GIF
```

Releasing to PyPI: bump `version` in `pyproject.toml` and `__version__` in `src/twisk/__init__.py`, push to main,
then run the "Publish to PyPI" workflow (`.github/workflows/publish.yml`, Trusted Publishing, environment `pypi`).

After UI changes, regenerate `docs/assets/` with the capture script (it uses the demo backend; the demo data
lives in `backend/fake.py` and `seed_specs()` in the script) and look at the GIF before committing.

## Architecture

- `specs/` — Pydantic specs (cluster, job, service, volume, pool) ⇄ plain SkyPilot YAML; `SpecStore` keeps them in
  `~/.twisk/specs/<kind>s/<name>.yaml`. Unknown YAML keys survive round trips via `extra` fields. twisk-only
  settings (a job's target pool) live in `# twisk: key=value` header comments so files stay valid SkyPilot YAML.
- `backend/base.py` — the `Backend` protocol and row dataclasses. `skypilot.py` implements it with the SDK (lazy
  `import sky`; health-checks the server first so the SDK never auto-starts a local server). `fake.py` is a
  time-driven simulation used by `--demo` and the tests — keep it in sync when adding backend methods.
- `tui/views.py` — declarative resource views (columns, `fetch`, `fetch_extra(ctx, rows)`, `cells`, actions).
  `tui/screens/main.py` dispatches view actions to `act_<name>` methods.
- `templates/` — every form is a template (fields + Jinja2 YAML template). `model.py` (pydantic schema),
  `engine.py` (coerce/validate values → sandboxed render → prune → spec; `merge3` keeps YAML hand edits on form
  re-edits; `prefill` reads YAML back through each field's `path`), `registry.py` (built-ins in `builtin/*.yaml`,
  user files in `~/.twisk/templates` override by name). `tui/screens/form.py` renders any template; widget ids are
  `#f-<key>` / errors `#e-<key>`. Spec headers record `# twisk: template=…` and `# twisk-values: {json}`.
- `tui/widgets/vim.py` — `VimTextArea`, a modal (vim) TextArea used by `screens/editor.py` for the YAML escape
  hatch. It owns its `:`/`/` command line (no focus hand-off, so fast typing can't race) and keeps its own snapshot
  undo stack (one step per vim change). Drive it in tests with `area.handle(token)`.
- `sandbox.py` — Docker-based k3s + `berkeleyskypilot/skypilot` API server (shared netns), pod image preload,
  `--ca-bundle` for TLS-intercepting proxies, auto-detects cgroup v1 / restricted `oom_score_adj` hosts.

## Conventions

- Usage is shown as GPU-hours and idle time, never dollars (Kubernetes is unpriced without `kubernetes.pricing`).
- Idle = no pending/running jobs since the last job ended (SkyPilot autostop semantics); SSH sessions aren't seen.
- Changing a built-in form = editing `templates/builtin/<kind>.yaml` (keep field keys stable: tests and saved
  `twisk-values` headers use them). SkyPilot rules belong in the spec models, not in templates, so YAML edits and
  custom templates get them too.
- Keep the `skypilot` client pin and the sandbox's server image (`sandbox.SKY_IMAGE`) on the same minor version.
