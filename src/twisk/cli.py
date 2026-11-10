"""Command-line entry point: ``twisk`` (TUI), ``twisk sandbox ...``, ``twisk specs ...``."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from twisk import __version__

app = typer.Typer(
    help="twisk — a k9s-style terminal UI for SkyPilot.",
    no_args_is_help=False,
    add_completion=False,
    invoke_without_command=True,
    rich_markup_mode="rich",
)
sandbox_app = typer.Typer(help="Local k3s + SkyPilot API server sandbox for trying twisk.", no_args_is_help=True)
specs_app = typer.Typer(help="The YAML spec library twisk manages for you (~/.twisk/specs).", no_args_is_help=True)
app.add_typer(sandbox_app, name="sandbox")
app.add_typer(specs_app, name="specs")
templates_app = typer.Typer(help="Form templates (~/.twisk/templates): define your own create/edit forms.", no_args_is_help=True)
app.add_typer(templates_app, name="templates")

console = Console()


@app.callback()
def main(
    ctx: typer.Context,
    endpoint: Optional[str] = typer.Option(None, "--endpoint", "-e", help="SkyPilot API server URL (default: from config)."),
    demo: bool = typer.Option(False, "--demo", help="Run against an in-memory simulation; no server needed."),
    refresh: Optional[float] = typer.Option(None, "--refresh", help="Seconds between auto-refreshes."),
    view: str = typer.Option("clusters", "--view", "-v", help="Initial view: clusters, jobs, services, pools, volumes, storage, usage, specs, requests, infra, workspaces, templates."),
    all_users: Optional[bool] = typer.Option(None, "--all-users/--mine", help="Show everyone's resources, or only yours (default: config, else mine)."),
    version: bool = typer.Option(False, "--version", help="Print the version and exit."),
) -> None:
    """Launch the TUI (default when no sub-command is given)."""
    if version:
        console.print(f"twisk {__version__}")
        raise typer.Exit()
    if ctx.invoked_subcommand is not None:
        return
    from twisk.settings import load_settings, resolve_endpoint
    from twisk.specs.store import SpecStore
    from twisk.tui.app import TwiskApp

    settings = load_settings()
    if demo:
        from twisk.backend.fake import FakeBackend

        backend = FakeBackend()
        backend.all_users = settings.all_users if all_users is None else all_users
        source = "demo"
    else:
        from twisk.backend.skypilot import SkyPilotBackend

        url, source = resolve_endpoint(endpoint)
        backend = SkyPilotBackend(
            url, all_users=settings.all_users if all_users is None else all_users, workspace=settings.workspace
        )
    TwiskApp(
        backend=backend,
        store=SpecStore(),
        refresh_seconds=refresh or settings.refresh_seconds,
        initial_view=view,
        endpoint_source=source,
        editor=settings.editor,
    ).run()


# ── sandbox ──────────────────────────────────────────────────


@sandbox_app.command("up")
def sandbox_up(
    ca_bundle: Optional[Path] = typer.Option(
        None, "--ca-bundle", help="Extra CA bundle to trust (for TLS-intercepting corporate proxies).", exists=True, dir_okay=False
    ),
    cpus: Optional[float] = typer.Option(None, help="CPU limit for the k3s node (default: unlimited)."),
    memory: Optional[float] = typer.Option(None, help="Memory limit for the k3s node in GB (default: unlimited)."),
    no_default: bool = typer.Option(False, "--no-default", help="Don't make the sandbox twisk's default endpoint."),
    no_preload: bool = typer.Option(False, "--no-preload", help="Don't pre-load the SkyPilot pod image (first launch pulls it)."),
) -> None:
    """Create a k3s cluster with a SkyPilot API server wired to it (~3-5 min)."""
    from rich.live import Live

    from twisk import sandbox

    states: dict[str, str] = {s: "pending" for s in sandbox.STEPS}
    icons = {"pending": "[dim]○[/dim]", "running": "[yellow]◐[/yellow]", "done": "[green]✓[/green]", "failed": "[red]✗[/red]"}

    def render() -> Table:
        t = Table.grid(padding=(0, 1))
        for s in sandbox.STEPS:
            t.add_row(icons[states[s]], s if states[s] != "running" else f"[bold]{s}[/bold]")
        return t

    console.print(f"[bold]Creating twisk sandbox[/bold] [dim](k3s {sandbox.K3S_IMAGE.split(':')[-1]}, {sandbox.SKY_IMAGE})[/dim]")
    with Live(render(), console=console, refresh_per_second=8) as live:
        def progress(step: str, status: str) -> None:
            states[step] = status
            live.update(render())

        try:
            url = sandbox.up(progress, ca_bundle=ca_bundle, cpus=cpus, memory_gb=memory, set_default=not no_default, preload=not no_preload)
        except sandbox.SandboxError as exc:
            live.stop()
            console.print(f"\n[red bold]Sandbox setup failed:[/red bold] {exc}")
            raise typer.Exit(1)
    console.print(f"\n[green bold]Sandbox ready.[/green bold] SkyPilot API server: [cyan]{url}[/cyan]")
    console.print(f"  kubeconfig:   [cyan]{sandbox.kubeconfig_path()}[/cyan]")
    console.print("  next:         [bold]twisk[/bold]   (or: SKYPILOT_API_SERVER_ENDPOINT=" + url + " sky status)")
    console.print("  pods use [bold]infra: k8s[/bold]; keep requests small (e.g. 1 CPU / 1-2 GB).")


@sandbox_app.command("start")
def sandbox_start() -> None:
    """Restart a stopped sandbox (after a reboot or Docker restart)."""
    from twisk import sandbox

    with console.status("Starting k3s and the SkyPilot API server…"):
        try:
            url = sandbox.start()
        except sandbox.SandboxError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1)
    console.print(f"[green]Sandbox running.[/green] SkyPilot API server: [cyan]{url}[/cyan]")


@sandbox_app.command("down")
def sandbox_down() -> None:
    """Delete the sandbox containers (all clusters in it go away)."""
    from twisk import sandbox

    if sandbox.down():
        console.print("[green]Sandbox removed.[/green]")
    else:
        console.print("[dim]No sandbox found; cleaned up leftovers.[/dim]")


@sandbox_app.command("status")
def sandbox_status() -> None:
    """Show sandbox containers and API server health."""
    from twisk import sandbox

    st = sandbox.status()
    if not st.exists:
        console.print("[dim]No sandbox. Create one with[/dim] [bold]twisk sandbox up[/bold]")
        raise typer.Exit(1)
    if st.k3s != "running" or st.skypilot != "running":
        console.print("[yellow]Sandbox is stopped.[/yellow] Restart it with [bold]twisk sandbox start[/bold]")
    t = Table(show_header=False, box=None)
    t.add_row("k3s", st.k3s or "-")
    t.add_row("skypilot", st.skypilot or "-")
    t.add_row("endpoint", f"{st.endpoint} ({'[green]healthy[/green]' if st.healthy else '[red]unreachable[/red]'})")
    t.add_row("version", st.server_version or "-")
    t.add_row("kubeconfig", str(st.kubeconfig or "-"))
    console.print(t)


@sandbox_app.command("preload")
def sandbox_preload(images: Optional[list[str]] = typer.Argument(None, help="Images to load (default: SkyPilot's pod image).")) -> None:
    """Pull images on the host and import them into the sandbox's k3s node."""
    from twisk import sandbox

    targets = images or list(sandbox.POD_IMAGES)
    with console.status(f"Loading {', '.join(targets)} into k3s…"):
        try:
            sandbox.preload_images(targets)
        except sandbox.SandboxError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1)
    console.print("[green]Images loaded.[/green]")


@sandbox_app.command("logs")
def sandbox_logs(tail: int = typer.Option(200, help="Number of lines.")) -> None:
    """Print the SkyPilot API server logs."""
    from twisk import sandbox

    console.print(sandbox.server_logs(tail), markup=False, highlight=False)


# ── specs ────────────────────────────────────────────────────


@specs_app.command("path")
def specs_path() -> None:
    """Print the spec library directory."""
    from twisk.specs.store import SpecStore

    print(SpecStore().root)


@specs_app.command("list")
def specs_list() -> None:
    """List stored specs."""
    from twisk.specs.store import SpecStore

    store = SpecStore()
    t = Table("KIND", "NAME", "VALID", "PATH")
    for s in store.list():
        t.add_row(s.kind.value, s.name, "[green]yes[/green]" if s.spec else f"[red]{s.error}[/red]", str(s.path))
    console.print(t)


@specs_app.command("edit")
def specs_edit(kind: str = typer.Argument(..., help="cluster | job | service | volume"), name: str = typer.Argument(...)) -> None:
    """Open a spec in $EDITOR and validate it afterwards."""
    from twisk.specs.models import SpecKind, spec_from_yaml
    from twisk.specs.store import SpecStore

    try:
        k = SpecKind(kind.rstrip("s"))
    except ValueError:
        console.print(f"[red]unknown kind {kind!r}[/red]")
        raise typer.Exit(2)
    store = SpecStore()
    path = store.path_for(k, name)
    if not path.exists():
        console.print(f"[red]{path} does not exist[/red]")
        raise typer.Exit(1)
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
    subprocess.call([*editor.split(), str(path)])
    try:
        spec_from_yaml(k, name, path.read_text())
    except Exception as exc:  # noqa: BLE001
        console.print(f"[yellow]Saved, but twisk can't parse it yet:[/yellow] {exc}")
        raise typer.Exit(1)
    console.print("[green]Valid.[/green]")


# ── templates ────────────────────────────────────────────────


@templates_app.command("list")
def templates_list() -> None:
    """List form templates (built-in and yours)."""
    from twisk.templates import TemplateRegistry

    registry = TemplateRegistry()
    builtins = {p.stem for p in registry.builtin_dir.glob("*.yaml")}
    t = Table("KIND", "NAME", "TITLE", "SOURCE", "FIELDS")
    for tpl in registry.all():
        source = "built-in" if tpl.source == "builtin" else ("yours (overrides built-in)" if tpl.name in builtins else "yours")
        t.add_row(tpl.kind.value, tpl.name, tpl.display_title, source, str(len(tpl.fields)))
    for b in registry.broken():
        t.add_row("?", b.name, f"[red]{b.error}[/red]", "yours", "-")
    console.print(t)
    console.print(f"[dim]Your templates: {registry.user_dir}[/dim]")


@templates_app.command("init")
def templates_init(force: bool = typer.Option(False, "--force", help="Overwrite your existing copies.")) -> None:
    """Copy the built-in forms to ~/.twisk/templates so you can customize them."""
    from twisk.templates import TemplateRegistry

    registry = TemplateRegistry()
    written = registry.init(overwrite=force)
    for path in written:
        console.print(f"[green]wrote[/green] {path}")
    if not written:
        console.print(f"[dim]Nothing to do — copies already exist in {registry.user_dir} (use --force to reset).[/dim]")
    else:
        console.print("Edit them (or add new ones next to them); twisk picks them up on the next form you open.")


@templates_app.command("path")
def templates_path() -> None:
    """Print the user template directory."""
    from twisk.templates import TemplateRegistry

    print(TemplateRegistry().user_dir)


@templates_app.command("validate")
def templates_validate() -> None:
    """Check every user template; exits non-zero if any is broken."""
    from twisk.templates import TemplateRegistry, default_values, materialize

    registry = TemplateRegistry()
    ok = True
    for tpl in registry.all():
        if tpl.source != "user":
            continue
        result = materialize(tpl, default_values(tpl) | {"name": "template-check"})
        problems = {k: v for k, v in result.errors.items() if k != "name"}
        if problems and result.spec is None:
            console.print(f"[yellow]⚠ {tpl.name}[/yellow]: defaults don't render a valid {tpl.kind.value}: {problems}")
        else:
            console.print(f"[green]✓ {tpl.name}[/green]")
    for b in registry.broken():
        ok = False
        console.print(f"[red]✗ {b.name}[/red]: {b.error}")
    if not ok:
        raise typer.Exit(1)


if __name__ == "__main__":  # pragma: no cover
    app()
