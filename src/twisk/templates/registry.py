"""Where templates come from: built-ins shipped with twisk + ~/.twisk/templates."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from twisk.settings import twisk_home
from twisk.specs.models import SpecKind
from twisk.templates.model import Template, TemplateError, load_template, parse_template

BUILTIN_DIR = Path(__file__).parent / "builtin"


def templates_dir() -> Path:
    return twisk_home() / "templates"


@dataclass(frozen=True)
class BrokenTemplate:
    """A user template file that failed to load (shown in the UI with its error)."""

    name: str
    path: Path
    error: str


class TemplateRegistry:
    """Built-in templates (one per kind, named after it) overlaid by user templates.

    A user file with the same name as a built-in (e.g. ``cluster.yaml``)
    replaces it, so ``twisk templates init`` + editing is how you change the
    default forms.
    """

    def __init__(self, user_dir: Path | None = None, builtin_dir: Path = BUILTIN_DIR) -> None:
        self.user_dir = Path(user_dir) if user_dir is not None else templates_dir()
        self.builtin_dir = builtin_dir

    def _load(self) -> tuple[dict[str, Template], list[BrokenTemplate]]:
        templates: dict[str, Template] = {}
        broken: list[BrokenTemplate] = []
        for path in sorted(self.builtin_dir.glob("*.yaml")):
            templates[path.stem] = load_template(path, source="builtin")
        if self.user_dir.is_dir():
            for path in sorted(self.user_dir.glob("*.yaml")):
                try:
                    templates[path.stem] = load_template(path, source="user")
                except (TemplateError, OSError) as exc:
                    broken.append(BrokenTemplate(path.stem, path, str(exc)))
        return templates, broken

    def all(self) -> list[Template]:
        return sorted(self._load()[0].values(), key=lambda t: (t.kind.value, t.name != t.kind.value, t.name))

    def broken(self) -> list[BrokenTemplate]:
        return self._load()[1]

    def for_kind(self, kind: SpecKind) -> list[Template]:
        """Templates for a kind; the default (named after the kind) first."""
        return [t for t in self.all() if t.kind == kind]

    def get(self, name: str) -> Template | None:
        return self._load()[0].get(name)

    def default(self, kind: SpecKind) -> Template:
        t = self.get(kind.value)
        if t is not None and t.kind == kind:
            return t
        return load_template(self.builtin_dir / f"{kind.value}.yaml", source="builtin")

    def builtin(self, name: str) -> Template | None:
        path = self.builtin_dir / f"{name}.yaml"
        return load_template(path, source="builtin") if path.exists() else None

    # ── editing ─────────────────────────────────────────────

    def user_path(self, name: str) -> Path:
        return self.user_dir / f"{name}.yaml"

    def text(self, name: str) -> str:
        """Source of the effective template (user copy if any, else built-in)."""
        user = self.user_path(name)
        if user.exists():
            return user.read_text()
        return (self.builtin_dir / f"{name}.yaml").read_text()

    def validate_text(self, name: str, text: str) -> Template:
        return parse_template(text, name)

    def save_text(self, name: str, text: str) -> Template:
        template = self.validate_text(name, text)
        self.user_dir.mkdir(parents=True, exist_ok=True)
        self.user_path(name).write_text(text if text.endswith("\n") else text + "\n")
        return template

    def delete(self, name: str) -> None:
        self.user_path(name).unlink(missing_ok=True)

    def init(self, overwrite: bool = False) -> list[Path]:
        """Copy the built-ins to the user dir as a starting point for customizing."""
        self.user_dir.mkdir(parents=True, exist_ok=True)
        written = []
        for path in sorted(self.builtin_dir.glob("*.yaml")):
            target = self.user_dir / path.name
            if target.exists() and not overwrite:
                continue
            shutil.copyfile(path, target)
            written.append(target)
        return written


STARTER = """\
# A custom twisk form. Field values are available in `template` by key;
# `| yaml` renders any value as YAML and empty values are dropped.
# Docs: the "Templates" section of the twisk README.
kind: {kind}
title: {title}
description: describe when to use this form
fields:
  - {{key: name, label: Name, required: true}}
  - key: gpu
    label: GPU
    type: select
    default: L4
    options: [L4, A100, H100]
  - {{key: gpus, label: GPUs, type: int, default: 1, min: 1, max: 8}}
  - {{key: run, label: Command, type: area, default: "nvidia-smi"}}
template: |
  name: {{{{ name }}}}
  resources:
    infra: k8s
    accelerators: {{{{ gpu }}}}:{{{{ gpus }}}}
    cpus: {{{{ gpus * 4 }}}}+
  run: {{{{ run | yaml }}}}
"""


def starter_text(kind: SpecKind) -> str:
    return STARTER.format(kind=kind.value, title=f"Custom {kind.title.lower()}")
