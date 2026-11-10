"""End-to-end TUI tests driven by Textual's pilot against the fake backend."""

from __future__ import annotations

import pytest
from textual.widgets import DataTable, Input, Select, TextArea

from twisk.backend.fake import FakeBackend
from twisk.specs.models import ClusterSpec, Resources, SpecKind
from twisk.specs.store import SpecStore
from twisk.tui.app import TwiskApp
from twisk.tui.screens.detail import DetailScreen
from twisk.tui.screens.dialogs import ConfirmScreen
from twisk.tui.screens.editor import YamlEditorScreen
from twisk.tui.screens.form import FormScreen
from twisk.tui.screens.logs import LogScreen
from twisk.tui.screens.main import MainScreen

SIZE = (160, 48)


@pytest.fixture
def store(tmp_path):
    return SpecStore(tmp_path / "specs")


@pytest.fixture
def backend():
    return FakeBackend(speed=40)


def make_app(backend, store, view="clusters"):
    return TwiskApp(backend, store=store, refresh_seconds=0.5, initial_view=view, endpoint_source="test", editor="")


async def settle(pilot, seconds=0.4):
    await pilot.pause(seconds)


def table_names(app) -> list[str]:
    table = app.screen.query_one(DataTable)
    return [str(table.get_row_at(i)[0]) for i in range(table.row_count)]


async def type_text(pilot, text):
    for ch in text:
        await pilot.press(ch if ch != " " else "space")


async def select_row(pilot, app, name):
    table = app.screen.query_one(DataTable)
    for i in range(table.row_count):
        if str(table.get_row_at(i)[0]) == name or str(table.get_row_at(i)[1]) == name:
            table.move_cursor(row=i)
            await pilot.pause(0.05)
            return
    raise AssertionError(f"{name} not in {table_names(app)}")


async def test_initial_clusters_view(backend, store):
    app = make_app(backend, store)
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        assert isinstance(app.screen, MainScreen)
        assert set(table_names(app)) == {"cpu-box", "dev-gpu", "idle-h100"}


async def test_create_cluster_via_form_saves_spec_and_launches(backend, store):
    app = make_app(backend, store)
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        await pilot.press("c")
        await settle(pilot, 0.2)
        assert isinstance(app.screen, FormScreen)
        await type_text(pilot, "my-dev")
        app.screen.query_one("#f-accelerators", Input).value = "L4:1"
        app.screen.query_one("#f-run", TextArea).text = "python train.py"
        await pilot.press("ctrl+s")
        await settle(pilot, 0.3)
        assert isinstance(app.screen, LogScreen)
        spec = store.get(SpecKind.CLUSTER, "my-dev")
        assert spec is not None and spec.resources.accelerators == "L4:1" and spec.run == "python train.py"
        await pilot.press("escape")
        await settle(pilot, 0.8)
        assert "my-dev" in table_names(app)


async def test_form_shows_validation_errors(backend, store):
    app = make_app(backend, store)
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        await pilot.press("c")
        await settle(pilot, 0.2)
        await type_text(pilot, "Bad Name")
        app.screen.query_one("#f-cpus", Input).value = "lots"
        await pilot.press("ctrl+s")
        await settle(pilot, 0.2)
        assert isinstance(app.screen, FormScreen)
        assert app.screen.query_one("#e-name").display
        assert app.screen.query_one("#e-cpus").display
        assert not store.list()
        await pilot.press("escape")
        await settle(pilot, 0.2)
        assert isinstance(app.screen, MainScreen)


async def test_save_only_then_launch_from_spec_library(backend, store):
    app = make_app(backend, store)
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        await pilot.press("c")
        await settle(pilot, 0.2)
        await type_text(pilot, "later")
        await pilot.press("ctrl+o")
        await settle(pilot, 0.3)
        assert isinstance(app.screen, MainScreen)
        assert store.exists(SpecKind.CLUSTER, "later")
        assert "later" not in table_names(app)
        await pilot.press("8")
        await settle(pilot)
        await select_row(pilot, app, "later")
        await pilot.press("enter")
        await settle(pilot, 0.2)
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.press("y")
        await settle(pilot, 0.4)
        assert isinstance(app.screen, LogScreen)
    assert any(c.name == "later" for c in backend.list_clusters())


async def test_command_mode_and_filter(backend, store):
    app = make_app(backend, store)
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        await pilot.press("colon")
        await type_text(pilot, "svc")
        await pilot.press("enter")
        await settle(pilot)
        main = app.screen
        assert main.view.name == "services"
        assert table_names(app) == ["llm-api"]
        await pilot.press("1")
        await settle(pilot)
        await pilot.press("slash")
        await type_text(pilot, "gpu")
        await pilot.press("enter")
        await settle(pilot, 0.2)
        assert table_names(app) == ["dev-gpu"]
        await pilot.press("escape")
        await settle(pilot, 0.2)
        assert len(table_names(app)) == 3
        await pilot.press("colon")
        await type_text(pilot, "nonsense")
        await pilot.press("enter")
        await settle(pilot, 0.2)
        assert main.view.name == "clusters"


async def test_drill_into_cluster_jobs_and_back(backend, store):
    app = make_app(backend, store)
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        await select_row(pilot, app, "dev-gpu")
        await pilot.press("enter")
        await settle(pilot)
        assert app.screen.view.crumb == "jobs@dev-gpu"
        assert len(table_names(app)) == 2
        await pilot.press("l")
        await settle(pilot, 0.3)
        assert isinstance(app.screen, LogScreen)
        await pilot.press("escape")
        await settle(pilot, 0.2)
        await pilot.press("escape")
        await settle(pilot)
        assert app.screen.view.name == "clusters"


async def test_stop_start_and_down(backend, store):
    app = make_app(backend, store)
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        # Kubernetes clusters can't be stopped: guarded, no dialog
        await select_row(pilot, app, "dev-gpu")
        await pilot.press("p")
        await settle(pilot, 0.2)
        assert isinstance(app.screen, MainScreen)
        # start a stopped cloud cluster, then stop it (with confirmation)
        await select_row(pilot, app, "cpu-box")
        await pilot.press("u")
        await settle(pilot, 0.3)
        assert isinstance(app.screen, LogScreen)
        await pilot.press("escape")
        await settle(pilot, 0.8)
        assert {c.name: c.status for c in backend.list_clusters()}["cpu-box"] == "UP"
        await select_row(pilot, app, "cpu-box")
        await pilot.press("p")
        await settle(pilot, 0.2)
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.press("escape")  # cancel → nothing happens
        await settle(pilot, 0.2)
        assert {c.name: c.status for c in backend.list_clusters()}["cpu-box"] == "UP"
        await pilot.press("p")
        await settle(pilot, 0.2)
        await pilot.press("y")
        await settle(pilot, 0.8)
        assert {c.name: c.status for c in backend.list_clusters()}["cpu-box"] == "STOPPED"
        # tear down requires confirmation
        await select_row(pilot, app, "dev-gpu")
        await pilot.press("ctrl+d")
        await settle(pilot, 0.2)
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.press("y")
        await settle(pilot, 1.0)
        assert "dev-gpu" not in [c.name for c in backend.list_clusters()]


async def test_edit_existing_cluster_imports_server_yaml(backend, store):
    app = make_app(backend, store)
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        await select_row(pilot, app, "dev-gpu")
        await pilot.press("e")
        await settle(pilot, 0.3)
        assert isinstance(app.screen, FormScreen)
        assert app.screen.query_one("#f-name", Input).disabled
        assert app.screen.query_one("#f-accelerators", Input).value == "L4:1"
        app.screen.query_one("#f-cpus", Input).value = "8"
        await pilot.press("ctrl+o")
        await settle(pilot, 0.3)
    spec = store.get(SpecKind.CLUSTER, "dev-gpu")
    assert spec.resources.cpus == "8" and spec.resources.accelerators == "L4:1"


async def vim(pilot, keys: str, *specials: str) -> None:
    """Type ``keys`` character by character, then press any named ``specials``."""
    from textual.keys import _character_to_key

    for ch in keys:
        await pilot.press(_character_to_key(ch) if not ch.isalnum() else ch)
    for key in specials:
        await pilot.press(key)


async def test_vim_editor_edits_existing_spec(backend, store):
    store.save(ClusterSpec(name="dev-gpu", resources=Resources(cpus="1")))
    app = make_app(backend, store)
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        await select_row(pilot, app, "dev-gpu")
        await pilot.press("E")
        await settle(pilot, 0.3)
        assert isinstance(app.screen, YamlEditorScreen)
        editor = app.screen.editor
        assert editor.mode == "normal"
        await vim(pilot, "ggO# hand-tuned", "escape")
        await vim(pilot, "/cpus", "enter")
        await vim(pilot, "$r3")
        await vim(pilot, "oinstance_type: custom", "escape")
        assert app.screen.modified
        await vim(pilot, ":q", "enter")  # refuses: unsaved changes
        await settle(pilot, 0.1)
        assert isinstance(app.screen, YamlEditorScreen)
        await vim(pilot, ":wq", "enter")
        await settle(pilot, 0.3)
        assert isinstance(app.screen, MainScreen)
    text = store.path_for(SpecKind.CLUSTER, "dev-gpu").read_text()
    assert text.startswith("# hand-tuned")
    spec = store.get(SpecKind.CLUSTER, "dev-gpu")
    assert spec.resources.cpus == "3"
    assert spec.resources.extra == {"instance_type": "custom"}


async def test_vim_editor_rejects_invalid_yaml(backend, store):
    store.save(ClusterSpec(name="dev-gpu"))
    before = store.path_for(SpecKind.CLUSTER, "dev-gpu").read_text()
    app = make_app(backend, store)
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        await select_row(pilot, app, "dev-gpu")
        await pilot.press("E")
        await settle(pilot, 0.3)
        await vim(pilot, "Gores: [", "escape")
        await vim(pilot, ":w", "enter")
        await settle(pilot, 0.2)
        assert isinstance(app.screen, YamlEditorScreen)  # stayed open
        assert "Not written" in str(app.screen.query_one("#editor-status").render())
        await vim(pilot, ":q!", "enter")
        await settle(pilot, 0.2)
        assert isinstance(app.screen, MainScreen)
    assert store.path_for(SpecKind.CLUSTER, "dev-gpu").read_text() == before


async def test_vim_editor_for_resource_about_to_be_created(backend, store):
    app = make_app(backend, store)
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        await pilot.press("c")
        await settle(pilot, 0.2)
        await type_text(pilot, "custom")
        await pilot.press("ctrl+e")
        await settle(pilot, 0.3)
        assert isinstance(app.screen, YamlEditorScreen) and app.screen.is_new
        await vim(pilot, ":q!", "enter")  # discard: nothing written
        await settle(pilot, 0.2)
        assert not store.exists(SpecKind.CLUSTER, "custom")

        await pilot.press("c")
        await settle(pilot, 0.2)
        await type_text(pilot, "custom")
        await pilot.press("ctrl+e")
        await settle(pilot, 0.3)
        # append a top-level key the form doesn't have: drop to col 0 first
        await vim(pilot, "Go", "backspace", "backspace")
        await vim(pilot, "file_mounts:", "enter")
        await vim(pilot, "  /data: s3://my-bucket", "escape")
        await vim(pilot, ":apply", "enter")
        await settle(pilot, 0.5)
        assert isinstance(app.screen, LogScreen)  # launched
    spec = store.get(SpecKind.CLUSTER, "custom")
    assert spec.extra == {"file_mounts": {"/data": "s3://my-bucket"}}
    assert "custom" in [c.name for c in backend.list_clusters()]


async def test_vim_editor_ctrl_s_saves_and_applies(backend, store):
    store.save(ClusterSpec(name="dev-gpu", resources=Resources(cpus="1")))
    app = make_app(backend, store)
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        await select_row(pilot, app, "dev-gpu")
        await pilot.press("E")
        await settle(pilot, 0.3)
        await vim(pilot, "/cpus", "enter")
        await vim(pilot, "$r2")
        await pilot.press("ctrl+s")
        await settle(pilot, 0.4)
        assert isinstance(app.screen, LogScreen)
    assert store.get(SpecKind.CLUSTER, "dev-gpu").resources.cpus == "2"


async def test_yaml_and_describe_views(backend, store):
    app = make_app(backend, store)
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        await select_row(pilot, app, "dev-gpu")
        await pilot.press("y")
        await settle(pilot, 0.2)
        assert isinstance(app.screen, DetailScreen)
        await pilot.press("escape")
        await pilot.press("d")
        await settle(pilot, 0.2)
        assert isinstance(app.screen, DetailScreen)


async def test_managed_job_create_and_cancel(backend, store):
    app = make_app(backend, store, view="jobs")
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        await pilot.press("c")
        await settle(pilot, 0.2)
        await type_text(pilot, "train")
        await pilot.press("ctrl+s")
        await settle(pilot, 0.3)
        assert isinstance(app.screen, LogScreen)
        await pilot.press("escape")
        await settle(pilot, 0.6)
        await select_row(pilot, app, "train")
        await pilot.press("ctrl+d")
        await settle(pilot, 0.2)
        await pilot.press("y")
        await settle(pilot, 0.6)
    job = next(j for j in backend.list_managed_jobs() if j.name == "train")
    assert job.status in ("CANCELLING", "CANCELLED")


async def test_service_form_and_replicas_drill(backend, store):
    app = make_app(backend, store, view="services")
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        await select_row(pilot, app, "llm-api")
        await pilot.press("enter")
        await settle(pilot)
        assert app.screen.view.crumb == "replicas@llm-api"
        assert len(table_names(app)) == 2
        await pilot.press("escape")
        await settle(pilot)
        await pilot.press("c")
        await settle(pilot, 0.2)
        await type_text(pilot, "echo")
        await pilot.press("ctrl+s")
        await settle(pilot, 0.3)
        assert isinstance(app.screen, LogScreen)
    assert store.exists(SpecKind.SERVICE, "echo")
    assert "echo" in [s.name for s in backend.list_services()]


async def test_volume_create_with_select(backend, store):
    app = make_app(backend, store, view="volumes")
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        await pilot.press("c")
        await settle(pilot, 0.2)
        await type_text(pilot, "scratch")
        app.screen.query_one("#f-access_mode", Select).value = "ReadWriteMany"
        await pilot.press("ctrl+s")
        await settle(pilot, 0.3)
    spec = store.get(SpecKind.VOLUME, "scratch")
    assert spec.access_mode == "ReadWriteMany"
    assert "scratch" in [v.name for v in backend.list_volumes()]


async def test_requests_view_follow_logs(backend, store):
    backend.launch_cluster(ClusterSpec(name="x"))
    app = make_app(backend, store, view="requests")
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        assert table_names(app)
        await pilot.press("enter")
        await settle(pilot, 0.3)
        assert isinstance(app.screen, LogScreen)


async def test_server_down_shows_error_not_crash(backend, store):
    backend.healthy = False
    app = make_app(backend, store)
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        empty = app.screen.query_one("#empty")
        assert empty.display
        assert "unavailable" in str(empty.render()).lower()
        # the spec library still works offline
        await pilot.press("8")
        await settle(pilot)
        assert not app.screen.error


async def test_help_and_quit(backend, store):
    app = make_app(backend, store)
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        await pilot.press("question_mark")
        await settle(pilot, 0.2)
        assert type(app.screen).__name__ == "HelpScreen"
        await pilot.press("escape")
        await pilot.press("colon")
        await type_text(pilot, "q")
        await pilot.press("enter")
        await settle(pilot, 0.2)
    assert app.return_code == 0 or app.return_code is None


async def test_narrow_terminal_hides_columns(backend, store):
    app = make_app(backend, store)
    async with app.run_test(size=(90, 40)) as pilot:
        await settle(pilot)
        table = app.screen.query_one(DataTable)
        labels = [str(c.label) for c in table.columns.values()]
        assert "INFRA" not in labels and "NAME" in labels


async def test_usage_view_summary_and_window(backend, store):
    app = make_app(backend, store, view="usage")
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        names = table_names(app)
        assert names[0] == "sweep-a100"  # sorted by GPU-hours
        title = str(app.screen.query_one("#title-bar").render())
        assert "GPU-hrs" in title and "GPUs up now" in title and "idle ≥1h" in title
        assert "$" not in title
        await pilot.press("t")  # 30d → all time
        await pilot.press("t")  # all → 1d
        await settle(pilot)
        assert app.screen.view.days == 1
        assert "sweep-a100" not in table_names(app)


async def test_clusters_show_gpus_idle_and_scope_toggle(backend, store):
    app = make_app(backend, store)
    async with app.run_test(size=(200, 48)) as pilot:
        await settle(pilot, 0.6)
        table = app.screen.query_one(DataTable)
        labels = [str(c.label) for c in table.columns.values()]
        assert {"GPUS", "GPU-HRS", "IDLE"} <= set(labels) and not any("$" in l for l in labels)
        idle_col = labels.index("IDLE")
        row = lambda name: [str(c) for c in table.get_row_at(table.get_row_index(name))]
        assert row("dev-gpu")[idle_col].startswith("busy")
        assert row("idle-h100")[idle_col].startswith("5h")
        assert row("cpu-box")[idle_col] == "-"
        title = str(app.screen.query_one("#title-bar").render())
        assert "9 GPUs up" in title and "8 idle" in title
        # sort by IDLE puts the longest-idle cluster last (ascending)
        while app.screen.sort_col != app.screen.view.columns.index(next(c for c in app.screen.view.columns if c.label == "IDLE")):
            await pilot.press("greater_than_sign")
        await settle(pilot, 0.2)
        assert table_names(app)[-1] == "idle-h100"
        assert "alice-train" not in table_names(app)
        await pilot.press("U")
        await settle(pilot, 0.6)
        assert "alice-train" in table_names(app)


async def test_pools_create_drill_and_job_in_pool(backend, store):
    app = make_app(backend, store, view="pools")
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        assert table_names(app) == ["eval-pool"]
        await pilot.press("enter")
        await settle(pilot)
        assert app.screen.view.crumb == "workers@eval-pool" and len(table_names(app)) == 2
        await pilot.press("l")
        await settle(pilot, 0.3)
        assert isinstance(app.screen, LogScreen)
        await pilot.press("escape")
        await pilot.press("escape")
        await settle(pilot)
        await pilot.press("c")
        await settle(pilot, 0.2)
        await type_text(pilot, "batch")
        app.screen.query_one("#f-workers", Input).value = "3"
        await pilot.press("ctrl+s")
        await settle(pilot, 0.5)
        await pilot.press("escape")
        await pilot.press("2")
        await settle(pilot)
        await pilot.press("c")
        await settle(pilot, 0.2)
        await type_text(pilot, "pooled")
        app.screen.query_one("#f-pool", Input).value = "batch"
        await pilot.press("ctrl+s")
        await settle(pilot, 0.5)
    assert {p.name: p.total_workers for p in backend.list_pools()}["batch"] == 3
    assert store.get(SpecKind.JOB, "pooled").pool == "batch"
    assert "pooled" in [j.name for j in backend.list_managed_jobs()]


async def test_storage_delete_and_workspace_switch(backend, store, tmp_path, monkeypatch):
    monkeypatch.setenv("TWISK_HOME", str(tmp_path / "home"))
    app = make_app(backend, store, view="storage")
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        await select_row(pilot, app, "sky-tmp-3f2a")
        await pilot.press("ctrl+d")
        await settle(pilot, 0.2)
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.press("y")
        await settle(pilot, 0.6)
        assert "sky-tmp-3f2a" not in table_names(app)
        await pilot.press("colon")
        await type_text(pilot, "ws")
        await pilot.press("enter")
        await settle(pilot)
        await select_row(pilot, app, "research")
        await pilot.press("enter")
        await settle(pilot, 0.6)
    assert [w.name for w in backend.list_workspaces() if w.active] == ["research"]
    from twisk.settings import load_settings

    assert load_settings().workspace == "research"


async def test_vim_style_table_navigation(backend, store):
    app = make_app(backend, store)
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        table = app.screen.query_one(DataTable)
        assert table.cursor_row == 0
        await pilot.press("j", "j")
        assert table.cursor_row == 2
        await pilot.press("k")
        assert table.cursor_row == 1
        await pilot.press("G")
        assert table.cursor_row == table.row_count - 1
        await pilot.press("g")
        assert table.cursor_row == 0


GPU_TEMPLATE = """\
kind: cluster
title: GPU dev box
description: one node on our k8s
fields:
  - {key: name, required: true}
  - {key: gpu, type: select, options: [L4, A100, H100], default: L4}
  - {key: gpus, type: int, default: 1, min: 1, max: 8}
template: |
  name: {{ name }}
  resources:
    infra: k8s
    accelerators: {{ gpu }}:{{ gpus }}
    cpus: {{ gpus * 8 }}+
"""


async def test_create_from_a_custom_template_and_edit_it_again(backend, store):
    from textual.widgets import Select

    from twisk.tui.screens.dialogs import TemplatePickerScreen

    app = make_app(backend, store)
    async with app.run_test(size=SIZE) as pilot:
        app.templates.save_text("gpu-box", GPU_TEMPLATE)
        await settle(pilot)
        await pilot.press("c")
        await settle(pilot, 0.2)
        assert isinstance(app.screen, TemplatePickerScreen)  # two cluster templates now
        await pilot.press("down", "enter")  # built-in first, then gpu-box
        await settle(pilot, 0.3)
        assert isinstance(app.screen, FormScreen) and app.screen.template.name == "gpu-box"
        assert [w.id for w in app.screen.query("Input, Select, Switch, TextArea")] == ["f-name", "f-gpu", "f-gpus"]
        await type_text(pilot, "box")
        app.screen.query_one("#f-gpu", Select).value = "H100"
        app.screen.query_one("#f-gpus", Input).value = "4"
        await pilot.press("ctrl+s")
        await settle(pilot, 0.4)
        assert isinstance(app.screen, LogScreen)
        await pilot.press("escape")
        await settle(pilot, 0.8)
        spec = store.get(SpecKind.CLUSTER, "box")
        assert spec.resources.accelerators == "H100:4" and spec.resources.cpus == "32+"
        assert store.form_meta(SpecKind.CLUSTER, "box")[0] == "gpu-box"
        # `e` reopens the same custom form with the values used
        await select_row(pilot, app, "box")
        await pilot.press("e")
        await settle(pilot, 0.3)
        assert app.screen.template.name == "gpu-box"
        assert app.screen.query_one("#f-gpus", Input).value == "4"
        assert app.screen.query_one("#f-gpu", Select).value == "H100"


async def test_form_edit_keeps_keys_added_in_the_yaml_editor(backend, store):
    app = make_app(backend, store)
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        await pilot.press("c")
        await settle(pilot, 0.2)
        await type_text(pilot, "dev")
        await pilot.press("ctrl+o")
        await settle(pilot, 0.3)
        await pilot.press("8")
        await settle(pilot)
        await select_row(pilot, app, "dev")
        await pilot.press("E")
        await settle(pilot, 0.3)
        await vim(pilot, "Go", "backspace", "backspace")  # top level, not under resources:
        await vim(pilot, "file_mounts:", "enter")
        await vim(pilot, "  /ckpt: s3://bucket", "escape")
        await vim(pilot, ":wq", "enter")
        await settle(pilot, 0.3)
        assert store.get(SpecKind.CLUSTER, "dev").extra["file_mounts"] == {"/ckpt": "s3://bucket"}
        await pilot.press("e")
        await settle(pilot, 0.3)
        app.screen.query_one("#f-cpus", Input).value = "6"
        await pilot.press("ctrl+o")
        await settle(pilot, 0.3)
    spec = store.get(SpecKind.CLUSTER, "dev")
    assert spec.resources.cpus == "6"
    assert spec.extra["file_mounts"] == {"/ckpt": "s3://bucket"}


async def test_templates_view_customizes_a_builtin(backend, store):
    app = make_app(backend, store, view="templates")
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        assert set(table_names(app)) >= {"cluster", "job", "service", "pool", "volume"}
        await select_row(pilot, app, "cluster")
        await pilot.press("E")
        await settle(pilot, 0.3)
        assert isinstance(app.screen, YamlEditorScreen) and app.screen.is_new
        await vim(pilot, "/title: Cluster", "enter")
        await vim(pilot, "$", "a")
        await vim(pilot, " (ours)", "escape")
        await vim(pilot, ":wq", "enter")
        await settle(pilot, 0.4)
        assert isinstance(app.screen, MainScreen)
        assert app.templates.default(SpecKind.CLUSTER).title == "Cluster (ours)"
        assert app.templates.default(SpecKind.CLUSTER).source == "user"
        # invalid template edits are refused
        await select_row(pilot, app, "job")
        await pilot.press("E")
        await settle(pilot, 0.3)
        await vim(pilot, "/^kind:", "enter")
        await vim(pilot, "dd")  # removes `kind:`
        await vim(pilot, ":w", "enter")
        await settle(pilot, 0.2)
        assert "Not written" in str(app.screen.query_one("#editor-status").render())
        await vim(pilot, ":q!", "enter")
        await settle(pilot, 0.2)
    assert not app.templates.user_path("job").exists()


class SlowBackend(FakeBackend):
    """A server slower than the refresh interval (0.5s in these tests)."""

    delay = 1.2

    def list_clusters(self, refresh: bool = False):
        import time

        time.sleep(self.delay)
        return super().list_clusters(refresh)


def title_text(app) -> str:
    return str(app.screen.query_one("#title-bar").render())


async def test_slow_server_loads_and_shows_progress(store):
    app = make_app(SlowBackend(speed=40), store)
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot, 0.3)
        empty = app.screen.query_one("#empty")
        # Never claims "No clusters" before the first answer.
        assert empty.display and "Loading clusters" in str(empty.render())
        # The 0.5s poll must not restart a 1.2s fetch forever.
        await settle(pilot, 2.5)
        assert "dev-gpu" in table_names(app)


async def test_returning_to_a_view_shows_cached_rows_while_refreshing(store):
    app = make_app(SlowBackend(speed=40), store)
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot, 2.5)
        assert "dev-gpu" in table_names(app)
        await pilot.press("2")  # managed jobs
        await settle(pilot, 0.5)
        await pilot.press("1")  # back to clusters: rows at once, refresh in the background
        await settle(pilot, 0.1)
        assert "dev-gpu" in table_names(app)
        assert not app.screen.query_one("#empty").display
        assert "refreshing" in title_text(app)
        await settle(pilot, 2.0)
        assert "refreshing" not in title_text(app)


async def _open_logs_and_wait(pilot, app, cluster, key, seconds=1.0):
    await select_row(pilot, app, cluster)
    await pilot.press(key)
    await settle(pilot, seconds)


def log_text(app) -> str:
    return "\n".join(app.screen._lines)


async def test_l_on_launching_cluster_follows_the_launch_request(store):
    backend = FakeBackend(speed=1)  # slow enough that the launch is still running
    backend.launch_cluster(ClusterSpec(name="booting"))
    app = make_app(backend, store)
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot, 0.8)
        await _open_logs_and_wait(pilot, app, "booting", "l")
        assert isinstance(app.screen, LogScreen)
        assert "booting launch request" in app.screen._title
        await settle(pilot, 3.0)
        assert "Launching on" in log_text(app)


async def test_l_on_up_cluster_shows_latest_job(backend, store):
    app = make_app(backend, store)
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        await _open_logs_and_wait(pilot, app, "dev-gpu", "l", 0.6)
        assert isinstance(app.screen, LogScreen)
        assert "latest job" in app.screen._title


async def test_v_tails_the_provisioner_log(backend, store):
    app = make_app(backend, store)
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        await _open_logs_and_wait(pilot, app, "dev-gpu", "v", 0.6)
        assert isinstance(app.screen, LogScreen)
        assert "provisioner log" in app.screen._title
        assert "Successfully provisioned cluster: dev-gpu" in log_text(app)


async def test_logs_menu_offers_all_three(backend, store):
    from twisk.tui.screens.dialogs import ClusterLogsScreen

    backend.launch_cluster(ClusterSpec(name="x"))
    app = make_app(backend, store)
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot, 0.8)
        await _open_logs_and_wait(pilot, app, "dev-gpu", "L", 0.3)
        assert isinstance(app.screen, ClusterLogsScreen)
        options = app.screen.query_one("#log-kinds")
        assert [options.get_option_at_index(i).id for i in range(options.option_count)] == ["request", "provision", "job"]
        assert options.highlighted == 2  # an UP cluster: latest job first
        await pilot.press("escape")
        await settle(pilot, 0.2)
        # A cluster with a launch request: pick it explicitly, even once it finished.
        await settle(pilot, 1.0)
        await _open_logs_and_wait(pilot, app, "x", "L", 0.3)
        await pilot.press("home", "enter")
        await settle(pilot, 1.0)
        assert isinstance(app.screen, LogScreen)
        assert "x launch request" in app.screen._title


async def test_request_log_missing_warns(backend, store):
    app = make_app(backend, store)
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        await _open_logs_and_wait(pilot, app, "dev-gpu", "L", 0.3)
        await pilot.press("home", "enter")
        await settle(pilot, 0.8)
        assert isinstance(app.screen, MainScreen)  # no request for a seeded cluster: a warning, no screen
