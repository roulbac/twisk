"""Form templates: model validation, rendering, merge, prefill, registry."""

from __future__ import annotations

import pytest
import yaml

from twisk.specs.models import ClusterSpec, JobSpec, Resources, ServiceSpec, SpecKind, VolumeSpec
from twisk.specs.store import SpecStore
from twisk.templates import (
    TemplateError,
    TemplateRegistry,
    default_values,
    materialize,
    parse_template,
    prefill,
    starter_text,
    values_to_raw,
)
from twisk.templates.engine import merge3, prune


@pytest.fixture
def registry(tmp_path):
    return TemplateRegistry(tmp_path / "templates")


GPU_BOX = """
kind: cluster
title: GPU dev box
description: one node on our k8s
fields:
  - {key: name, required: true}
  - {key: gpu, type: select, options: [L4, A100, H100], default: L4}
  - {key: gpus, type: int, default: 1, min: 1, max: 8, path: resources.accelerators}
  - {key: team, pattern: "[a-z]+", help: "lowercase team name"}
  - {key: env, type: kv}
  - {key: mounts, type: list}
template: |
  name: {{ name }}
  resources:
    infra: k8s
    accelerators: {{ gpu }}:{{ gpus }}
    cpus: {{ gpus * 8 }}+
  envs: {{ env | yaml }}
  {% if team %}
  config: {kubernetes: {pod_config: {metadata: {labels: {team: {{ team | yaml }}}}}}}
  {% endif %}
"""


# ── built-in templates reproduce the old forms ──────────────


@pytest.mark.parametrize("kind", list(SpecKind))
def test_builtin_defaults_render_valid_specs_and_roundtrip(registry, kind):
    t = registry.default(kind)
    assert t.kind == kind and t.source == "builtin"
    result = materialize(t, default_values(t) | {"name": "demo"})
    assert result.spec is not None, result.errors
    assert result.spec.name == "demo"
    again = materialize(t, prefill(t, result.spec))
    assert again.spec == result.spec


def test_builtin_cluster_maps_fields_to_yaml(registry):
    t = registry.default(SpecKind.CLUSTER)
    raw = default_values(t) | {
        "name": "dev", "infra": "k8s", "accelerators": "L4:1", "cpus": "4+", "ports": "8888, 6006",
        "autostop_minutes": "30", "autostop_down": True, "envs": "A=1\nB=two", "volumes": "/data=ds",
        "setup": "pip install x\npip install y", "run": "python train.py",
    }
    spec = materialize(t, raw).spec
    assert isinstance(spec, ClusterSpec)
    assert spec.resources.accelerators == "L4:1" and spec.resources.cpus == "4+"
    assert spec.resources.ports == ["8888", "6006"]
    assert spec.autostop_minutes == 30 and spec.autostop_down
    assert spec.envs == {"A": "1", "B": "two"} and spec.volumes == {"/data": "ds"}
    assert spec.setup == "pip install x\npip install y"


def test_field_errors_are_keyed(registry):
    t = registry.default(SpecKind.CLUSTER)
    errors = materialize(t, default_values(t) | {"name": "Bad Name", "disk_size": "x", "envs": "nope"}).errors
    assert {"name", "disk_size", "envs"} <= set(errors)
    # spec-level validation is mapped back to the field through `path`
    assert "cpus" in materialize(t, default_values(t) | {"name": "c", "cpus": "lots"}).errors


def test_k8s_autostop_requires_down(registry):
    t = registry.default(SpecKind.CLUSTER)
    raw = default_values(t) | {"name": "c", "infra": "k8s", "autostop_minutes": "30"}
    errors = materialize(t, raw).errors
    assert "autostop_minutes" in errors and "tear" in errors["autostop_minutes"]
    assert materialize(t, raw | {"autostop_down": True}).spec.autostop_down
    assert materialize(t, raw | {"infra": "aws"}).spec.autostop_minutes == 30  # clouds can stop
    with pytest.raises(Exception):  # the rule lives in the model: YAML can't bypass it
        ClusterSpec(name="c", resources=Resources(infra="k8s"), autostop_minutes=5)


def test_service_rules(registry):
    t = registry.default(SpecKind.SERVICE)
    assert "port" in materialize(t, default_values(t) | {"name": "svc", "port": "http"}).errors
    errors = materialize(t, default_values(t) | {"name": "svc", "max_replicas": "3"}).errors
    assert "target_qps_per_replica" in errors
    ok = materialize(t, default_values(t) | {"name": "svc", "max_replicas": "4", "target_qps_per_replica": "2.5",
                                             "min_replicas": "2", "ports": "9090"}).spec
    assert isinstance(ok, ServiceSpec) and ok.max_replicas == 4 and ok.resources.ports == ["9090"]
    back = prefill(t, ok)
    assert back["min_replicas"] == "2" and back["max_replicas"] == "4"


def test_job_pool_is_metadata_not_yaml(registry):
    t = registry.default(SpecKind.JOB)
    spec = materialize(t, default_values(t) | {"name": "j", "pool": "p"}).spec
    assert isinstance(spec, JobSpec) and spec.pool == "p"
    assert "pool" not in yaml.safe_load(spec.to_yaml())
    assert prefill(t, spec)["pool"] == "p"


def test_volume_template(registry):
    t = registry.default(SpecKind.VOLUME)
    spec = materialize(t, default_values(t) | {"name": "v", "size": "5Gi", "access_mode": "ReadWriteMany",
                                               "labels": "team=ml"}).spec
    assert isinstance(spec, VolumeSpec) and spec.access_mode == "ReadWriteMany" and spec.labels == {"team": "ml"}
    assert materialize(t, default_values(t) | {"name": "v"}).spec.access_mode is None  # "" option → omitted


# ── custom templates ────────────────────────────────────────


def test_custom_template_renders_arbitrary_skypilot_yaml():
    t = parse_template(GPU_BOX, "gpu-box")
    assert t.title == "GPU dev box" and [f.key for f in t.fields][:3] == ["name", "gpu", "gpus"]
    result = materialize(t, default_values(t) | {"name": "box", "gpu": "H100", "gpus": "4", "team": "ml",
                                                 "env": "WANDB=off"})
    spec = result.spec
    assert spec.resources.accelerators == "H100:4" and spec.resources.cpus == "32+"
    assert spec.envs == {"WANDB": "off"}
    assert spec.extra["config"]["kubernetes"]["pod_config"]["metadata"]["labels"] == {"team": "ml"}


def test_custom_template_field_validation():
    t = parse_template(GPU_BOX, "gpu-box")
    errors = materialize(t, default_values(t) | {"name": "box", "gpus": "9", "team": "ML!", "gpu": "TPU"}).errors
    assert errors["gpus"] == "must be ≤ 8"
    assert errors["team"] == "lowercase team name"
    assert "must be one of" in errors["gpu"]


@pytest.mark.parametrize("text,needle", [
    ("kind: cluster\nfields: []\ntemplate: x", "name"),
    ("kind: rocket\nfields: [{key: name}]\ntemplate: x", "kind"),
    ("kind: cluster\nfields: [{key: name}, {key: name}]\ntemplate: x", "duplicate"),
    ("kind: cluster\nfields: [{key: name}]\ntemplate: '{{ name '", "template line"),
    ("kind: cluster\nfields: [{key: name}, {key: x, type: select}]\ntemplate: x", "options"),
    ("kind: cluster\nfields: [{key: name, colour: red}]\ntemplate: x", "colour"),
    ("- just a list", "mapping"),
])
def test_bad_templates_explain_themselves(text, needle):
    with pytest.raises(TemplateError, match=needle):
        parse_template(text, "bad")


def test_rendering_errors_are_reported_not_raised():
    t = parse_template("kind: cluster\nfields: [{key: name}]\ntemplate: '{{ name }}: [oops'", "x")
    result = materialize(t, {"name": "c"})
    assert result.spec is None and "invalid YAML" in result.errors["_form"]


def test_templates_are_sandboxed():
    t = parse_template(
        "kind: cluster\nfields: [{key: name}]\n"
        "template: \"name: {{ name }}\\nrun: {{ ''.__class__.__mro__[1].__subclasses__() | yaml }}\"",
        "evil",
    )
    result = materialize(t, {"name": "c"})
    assert result.spec is None and "_form" in result.errors


def test_starter_template_is_valid():
    for kind in (SpecKind.CLUSTER, SpecKind.JOB):
        t = parse_template(starter_text(kind), "starter")
        assert materialize(t, default_values(t) | {"name": "x"}).spec is not None


# ── merge & prefill ─────────────────────────────────────────


def test_prune_drops_empty_values():
    assert prune({"a": None, "b": "", "c": [], "d": {}, "e": {"f": None}, "g": 0, "h": False}) == {"g": 0, "h": False}


def test_merge3_keeps_hand_edits_the_form_did_not_touch():
    base = {"resources": {"cpus": 1}, "run": "a"}
    ours = {"resources": {"cpus": 1, "instance_type": "x"}, "run": "a", "file_mounts": {"/d": "s3://b"}}
    theirs = {"resources": {"cpus": 4}, "run": "a"}
    assert merge3(ours, base, theirs) == {"resources": {"cpus": 4, "instance_type": "x"}, "run": "a",
                                          "file_mounts": {"/d": "s3://b"}}
    # a hand edit to a value the form also changed: the form wins
    assert merge3({"run": "hand"}, {"run": "a"}, {"run": "form"}) == {"run": "form"}
    # a hand edit the form didn't change survives
    assert merge3({"run": "hand"}, {"run": "a"}, {"run": "a"}) == {"run": "hand"}
    # the form removing a key it owned removes it
    assert merge3({"envs": {"A": "1"}}, {"envs": {"A": "1"}}, {}) == {}


def test_editing_through_form_preserves_yaml_only_keys(registry):
    t = registry.default(SpecKind.CLUSTER)
    first = materialize(t, default_values(t) | {"name": "dev", "cpus": "2"})
    base_values = values_to_raw(t, first.values)
    on_disk = first.spec.to_yaml_dict() | {"file_mounts": {"/ckpt": "s3://bucket"}}
    edited = materialize(t, base_values | {"cpus": "8"}, base_data=on_disk, base_values=base_values)
    assert edited.spec.resources.cpus == "8"
    assert edited.spec.extra["file_mounts"] == {"/ckpt": "s3://bucket"}


def test_prefill_reads_specs_made_elsewhere(registry):
    spec = ClusterSpec(name="imported", resources=Resources(infra="gcp", accelerators="A100:8", cpus="32+",
                                                            disk_size=200, ports=["8888"]),
                       envs={"X": "1"}, run="python a.py", autostop_minutes=10)
    t = registry.default(SpecKind.CLUSTER)
    raw = prefill(t, spec)
    assert raw["accelerators"] == "A100:8" and raw["disk_size"] == "200" and raw["ports"] == "8888"
    assert raw["envs"] == "X=1" and raw["autostop_minutes"] == "10" and raw["autostop_down"] is False
    assert materialize(t, raw).spec == spec


# ── registry ────────────────────────────────────────────────


def test_registry_user_templates_add_and_override(registry):
    assert [t.name for t in registry.for_kind(SpecKind.CLUSTER)] == ["cluster"]
    registry.save_text("gpu-box", GPU_BOX)
    assert [t.name for t in registry.for_kind(SpecKind.CLUSTER)] == ["cluster", "gpu-box"]
    override = registry.text("cluster").replace("title: Cluster", "title: Our cluster")
    registry.save_text("cluster", override)
    assert registry.default(SpecKind.CLUSTER).title == "Our cluster"
    assert registry.default(SpecKind.CLUSTER).source == "user"
    registry.delete("cluster")
    assert registry.default(SpecKind.CLUSTER).source == "builtin"


def test_registry_reports_broken_files(registry):
    registry.user_dir.mkdir(parents=True)
    (registry.user_dir / "oops.yaml").write_text("kind: cluster\nfields: []\ntemplate: x\n")
    assert [b.name for b in registry.broken()] == ["oops"]
    assert registry.get("oops") is None
    with pytest.raises(TemplateError):
        registry.save_text("oops", "kind: nope")


def test_registry_init_copies_builtins(registry):
    written = registry.init()
    assert sorted(p.stem for p in written) == ["cluster", "job", "pool", "service", "volume"]
    assert registry.init() == []  # doesn't overwrite your copies
    assert all(t.source == "user" for t in registry.all())


def test_store_records_template_and_values(tmp_path, registry):
    store = SpecStore(tmp_path / "specs")
    t = registry.default(SpecKind.CLUSTER)
    result = materialize(t, default_values(t) | {"name": "dev", "cpus": "3"})
    store.save(result.spec, t.name, result.values)
    template, values = store.form_meta(SpecKind.CLUSTER, "dev")
    assert template == "cluster" and values["cpus"] == "3"
    assert store.get(SpecKind.CLUSTER, "dev") == result.spec


def test_readme_example_template_works():
    """The documented example must stay valid."""
    import re
    from pathlib import Path

    readme = (Path(__file__).parent.parent / "README.md").read_text()
    block = re.search(r"```yaml\n(# ~/.twisk/templates/gpu-box.yaml\n.*?)```", readme, re.S).group(1)
    t = parse_template(block, "gpu-box")
    spec = materialize(t, default_values(t) | {"name": "box", "gpu": "H100", "gpus": "2", "team": "ml"}).spec
    assert spec is not None and spec.resources.accelerators == "H100:2"
