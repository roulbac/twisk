# Changelog

All notable changes to twisk. Versions follow [semantic versioning](https://semver.org/); each release's notes
below are also published as its [GitHub Release](https://github.com/roulbac/twisk/releases).

## [0.1.1] - 2026-11-10

### Changed

- `setup` and `run` are always written as `|` block scalars in the YAML twisk saves and previews, even when they
  are a single line.
- Any other value you write as a `|` or `>` block keeps that style when twisk rewrites the file: form re-edits,
  duplicated specs and templates that emit block scalars. Values you didn't write as blocks are left as they were.
  What twisk sends to SkyPilot is unchanged (plain values).

### Fixed

- Editing a spec whose environment variables have multi-line values no longer fails in the form. `KEY=value`
  fields accept multi-line values: indent a line by two spaces to continue the previous value.

## [0.1.0] - 2026-11-10

First public release: a k9s-style terminal UI for SkyPilot (API-server mode).

- **Forms, not YAML.** Create clusters, managed jobs, services, pools and volumes from validated forms with a live
  YAML preview.
- **Form templates.** Every form is a template in `~/.twisk/templates` (fields plus a Jinja2 YAML template), so teams
  can ship their own forms with their defaults baked in.
- **Spec library.** Everything is saved as plain SkyPilot YAML under `~/.twisk/specs`; edit it in a form (`e`), in
  the built-in vim editor (`E`), or relaunch from it (`R`). Unknown YAML keys survive form edits.
- **Live views.** Clusters and job queues, managed jobs, services and replicas, pools and workers, volumes, storage,
  API requests, infra and workspaces: auto-refreshing, filterable and sortable.
- **GPU-hours and idle time** per cluster (no dollar figures), with idle GPU clusters flagged and a `:usage` view.
- **Mine vs. everyone** (`U`) and workspace switching (`:ws`).
- **Logs and shells.** `l` streams logs: a cluster's launch request while it comes up, then its latest job; `v`
  tails the provisioner log; `L` picks between them. `s` opens an SSH shell, `x` runs `sky exec`.
- **Safe by default.** Destructive actions are on `ctrl-d` and always confirm; twisk never auto-starts a local API
  server.
- **Sandbox.** `twisk sandbox up` runs k3s plus a SkyPilot API server in Docker for trying twisk out.

[0.1.1]: https://github.com/roulbac/twisk/releases/tag/v0.1.1
[0.1.0]: https://github.com/roulbac/twisk/releases/tag/v0.1.0
