"""`|` blocks: setup/run always; elsewhere wherever the YAML used them."""

import yaml

from twisk.specs.models import ClusterSpec, JobSpec, Resources, SpecKind, spec_from_yaml
from twisk.specs.store import SpecStore
from twisk.templates import materialize, prefill
from twisk.templates.registry import TemplateRegistry

SOURCE = """\
resources:
  infra: k8s
envs:
  NOTE: |
    line one
    line two
  ONE_LINER: |-
    just one line
  PLAIN: "a\\nb"
file_mounts:
  /tmp/hello.sh: |
    #!/bin/sh
    echo hi
config:
  kubernetes:
    pod_config:
      metadata:
        annotations:
          note: >
            folded
            text
setup: pip install -r requirements.txt
run: python train.py
"""


def test_setup_and_run_always_use_literal_blocks():
    text = ClusterSpec(name="a", setup="pip install x", run="echo hi").to_yaml()
    assert "setup: |\n  pip install x\n" in text
    assert "run: |\n  echo hi\n" in text
    multi = JobSpec(name="j", resources=Resources(), run="a\nb").to_yaml()
    assert "run: |\n  a\n  b\n" in multi


def test_user_block_styles_survive_a_round_trip():
    spec = spec_from_yaml(SpecKind.CLUSTER, "c", SOURCE)
    out = spec.to_yaml()
    assert "  NOTE: |\n    line one\n    line two\n" in out
    assert "  ONE_LINER: |-\n    just one line\n" in out
    assert "  /tmp/hello.sh: |\n    #!/bin/sh\n    echo hi\n" in out
    assert "note: >\n" in out
    assert "setup: |\n  pip install -r requirements.txt\n" in out
    assert "run: |\n  python train.py\n" in out
    # Values written without a block stay as they were written (not forced into one).
    assert "PLAIN: |" not in out
    # Same data either way.
    assert yaml.safe_load(out) == {**yaml.safe_load(spec_from_yaml(SpecKind.CLUSTER, "c", out).to_yaml())}
    assert yaml.safe_load(out)["envs"]["NOTE"] == "line one\nline two\n"


def test_store_and_form_re_edit_keep_block_styles(tmp_path):
    store = SpecStore(tmp_path / "specs")
    store.save_text(SpecKind.CLUSTER, "c", SOURCE)
    spec = store.get(SpecKind.CLUSTER, "c")
    template = TemplateRegistry(tmp_path / "templates").default(SpecKind.CLUSTER)
    raw = prefill(template, spec)
    raw["run"] = "python train.py --epochs 2"  # change something in the form
    result = materialize(template, raw, base_data=spec.to_yaml_dict(), base_values=dict(prefill(template, spec)),
                         base_styles=spec._block_styles)
    assert result.spec is not None, result.errors
    store.save(result.spec)
    text = (tmp_path / "specs" / "clusters" / "c.yaml").read_text()
    assert "  NOTE: |\n    line one\n" in text
    assert "run: |\n  python train.py --epochs 2\n" in text
    assert store.get(SpecKind.CLUSTER, "c").envs["NOTE"] == "line one\nline two\n"


def test_template_written_blocks_are_kept(tmp_path):
    from twisk.templates.model import parse_template

    template = parse_template(
        "kind: cluster\nfields:\n  - {key: name, required: true}\n"
        "template: |\n  name: {{ name }}\n  envs:\n    BANNER: |\n      hello\n      world\n",
        "banner",
    )
    result = materialize(template, {"name": "b"})
    assert result.spec is not None, result.errors
    assert "  BANNER: |\n    hello\n    world\n" in result.spec.to_yaml()


def test_kv_lines_round_trip_multiline_values():
    from twisk.specs.models import format_kv_lines, parse_kv_lines

    data = {"A": "1", "NOTE": "line one\nline two", "B": "x=y"}
    text = format_kv_lines(data)
    assert text == "A=1\nNOTE=line one\n  line two\nB=x=y"
    assert parse_kv_lines(text) == data
    assert parse_kv_lines("# comment\n\nK=v\n") == {"K": "v"}
