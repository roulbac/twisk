"""Form templates: a form definition plus a Jinja2 template that renders YAML.

A template file (``~/.twisk/templates/<name>.yaml``) looks like::

    kind: cluster                 # cluster | job | service | pool | volume
    title: GPU dev box
    description: Single node on our Kubernetes cluster
    fields:
      - key: name                 # every template needs a `name` field
        label: Cluster name
        required: true
      - key: gpu
        type: select
        options: [L4, A100, H100]
        default: L4
      - key: gpus
        type: int
        default: 1
        min: 1
        max: 8
    template: |
      name: {{ name }}
      resources:
        infra: k8s
        accelerators: {{ gpu }}:{{ gpus }}
        cpus: {{ gpus * 8 }}+

Field values are available in the template under their ``key``; empty optional
fields are ``None``. The ``yaml`` filter renders any value as inline YAML
(``{{ envs | yaml }}``), and keys whose value renders as null or empty are
dropped, so optional fields need no ``{% if %}`` boilerplate.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from twisk.specs.models import SpecKind

FieldType = Literal["text", "area", "int", "float", "bool", "select", "list", "kv"]
SUGGESTION_SOURCES = ("infra", "accelerators", "volumes", "pools")
_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class TemplateError(Exception):
    """A template file is malformed (bad YAML, schema or Jinja syntax)."""


class Option(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str
    value: Any


class TemplateField(BaseModel):
    """One form field."""

    model_config = ConfigDict(extra="forbid")

    key: str
    label: str | None = None
    type: FieldType = "text"
    section: str = "General"
    default: Any = None
    required: bool = False
    help: str = ""
    placeholder: str = ""
    options: list[Option] = Field(default_factory=list)
    suggest: list[str] | str | None = None
    min: float | None = None
    max: float | None = None
    pattern: str | None = None
    # Where this field lives in the rendered YAML (dotted, e.g. resources.cpus).
    # Used to pre-fill the form when editing YAML twisk didn't render from this
    # template, and to attach validation errors to the right field.
    # `twisk.<name>` stores the value as twisk metadata instead (e.g. twisk.pool).
    path: str | None = None

    @field_validator("key")
    @classmethod
    def _key(cls, v: str) -> str:
        if not _KEY_RE.match(v):
            raise ValueError(f"field key {v!r} must be a valid identifier (letters, digits, _)")
        return v

    @field_validator("options", mode="before")
    @classmethod
    def _options(cls, v: Any) -> Any:
        # Accept `options: [a, b]` as shorthand for label == value.
        if isinstance(v, list):
            return [o if isinstance(o, dict) else {"label": str(o), "value": o} for o in v]
        return v

    @field_validator("pattern")
    @classmethod
    def _pattern(cls, v: str | None) -> str | None:
        if v is not None:
            try:
                re.compile(v)
            except re.error as exc:
                raise ValueError(f"invalid pattern: {exc}") from exc
        return v

    @model_validator(mode="after")
    def _check(self) -> "TemplateField":
        if self.type == "select" and not self.options:
            raise ValueError(f"field {self.key!r}: select fields need options")
        if isinstance(self.suggest, str) and self.suggest not in SUGGESTION_SOURCES:
            raise ValueError(f"field {self.key!r}: suggest must be a list or one of {', '.join(SUGGESTION_SOURCES)}")
        return self

    @property
    def title(self) -> str:
        return self.label or self.key.replace("_", " ").capitalize()

    @property
    def is_meta(self) -> bool:
        return bool(self.path and self.path.startswith("twisk."))


class Template(BaseModel):
    """A form + YAML template for one kind of resource."""

    model_config = ConfigDict(extra="forbid")

    name: str = ""  # from the file name
    kind: SpecKind
    title: str | None = None
    description: str = ""
    fields: list[TemplateField]
    template: str
    source: str = "user"  # "builtin" or "user"
    path: str | None = None

    @model_validator(mode="after")
    def _check(self) -> "Template":
        keys = [f.key for f in self.fields]
        dupes = {k for k in keys if keys.count(k) > 1}
        if dupes:
            raise ValueError(f"duplicate field keys: {', '.join(sorted(dupes))}")
        if "name" not in keys:
            raise ValueError("a template needs a field with key `name` (the resource name)")
        from twisk.templates.engine import compile_template  # noqa: PLC0415 - avoid cycle

        compile_template(self.template)  # raises TemplateError on Jinja syntax errors
        return self

    @property
    def display_title(self) -> str:
        return self.title or self.name

    def field(self, key: str) -> TemplateField | None:
        return next((f for f in self.fields if f.key == key), None)

    def sections(self) -> list[str]:
        seen: list[str] = []
        for f in self.fields:
            if f.section not in seen:
                seen.append(f.section)
        return seen


def parse_template(text: str, name: str, source: str = "user", path: str | None = None) -> Template:
    """Parse and validate template YAML, raising :class:`TemplateError` with a readable message."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise TemplateError(f"not valid YAML: {str(exc).splitlines()[0]}") from exc
    if not isinstance(data, dict):
        raise TemplateError("a template must be a YAML mapping with kind, fields and template")
    try:
        return Template.model_validate({**data, "name": name, "source": source, "path": path})
    except ValidationError as exc:
        err = exc.errors()[0]
        loc = ".".join(str(p) for p in err.get("loc", ()))
        msg = str(err.get("msg", "invalid")).removeprefix("Value error, ")
        raise TemplateError(f"{loc}: {msg}" if loc else msg) from None
    except TemplateError:
        raise


def load_template(path: Path, source: str = "user") -> Template:
    return parse_template(path.read_text(), path.stem, source=source, path=str(path))
