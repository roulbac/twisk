import pytest

from twisk.specs.models import ClusterSpec, JobSpec, Resources, ServiceSpec, SpecKind, VolumeSpec
from twisk.specs.store import SpecStore


@pytest.fixture
def store(tmp_path):
    return SpecStore(tmp_path / "specs")


def test_save_load_list(store):
    spec = ClusterSpec(name="dev", resources=Resources(cpus="2"))
    path = store.save(spec)
    assert path == store.root / "clusters" / "dev.yaml"
    text = path.read_text()
    assert text.startswith("# Managed by twisk")
    assert "sky launch -c dev" in text
    assert store.get(SpecKind.CLUSTER, "dev") == spec
    assert [s.name for s in store.list()] == ["dev"]
    assert store.names(SpecKind.CLUSTER) == {"dev"}


def test_invalid_file_is_listed_with_error(store):
    path = store.path_for(SpecKind.JOB, "broken")
    path.parent.mkdir(parents=True)
    path.write_text("name: broken\nresources: [1, 2\n")
    (entry,) = store.list(SpecKind.JOB)
    assert entry.spec is None and entry.error
    path.write_text("name: broken\n")  # valid YAML, missing run
    (entry,) = store.list(SpecKind.JOB)
    assert entry.spec is None and "run" in entry.error


def test_save_text_keeps_comments(store):
    text = "# my notes\nname: j\nrun: echo hi  # inline\n"
    spec = store.save_text(SpecKind.JOB, "j", text)
    assert isinstance(spec, JobSpec)
    assert store.path_for(SpecKind.JOB, "j").read_text() == text


def test_save_text_rejects_invalid(store):
    with pytest.raises(Exception):
        store.save_text(SpecKind.JOB, "j", "name: j\n")
    assert not store.exists(SpecKind.JOB, "j")


def test_history(store):
    store.record(SpecKind.CLUSTER, "dev", "launch", "req-1")
    assert (("cluster", "dev") in store.last_used())
