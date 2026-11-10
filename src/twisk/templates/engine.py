"""Turn form values into a validated spec through a template, and back.

Pipeline (``materialize``):

1. coerce + validate each field's raw form value (type, required, min/max, pattern, options)
2. render the Jinja2 template (sandboxed) with the values → YAML text
3. parse, prune null/empty keys, validate as a twisk spec (SkyPilot semantics)
4. when editing an existing spec, three-way merge so hand edits survive

Errors come back keyed by field (``"_form"`` for ones that can't be placed).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import yaml
from jinja2 import TemplateSyntaxError, Undefined
from jinja2.sandbox import SandboxedEnvironment
from pydantic import ValidationError

from twisk.specs.models import (
    SPEC_TYPES,
    JobSpec,
    Spec,
    SpecKind,
    block_styles,
    format_kv_lines,
    parse_kv_lines,
    set_block_styles,
)
from twisk.templates.model import Template, TemplateError, TemplateField

_MISSING = object()


def _yaml_filter(value: Any) -> str:
    """Render any value as inline YAML (JSON is valid YAML flow syntax)."""
    if isinstance(value, Undefined):
        value = None
    return json.dumps(value, ensure_ascii=False)


_env = SandboxedEnvironment(trim_blocks=True, lstrip_blocks=True, keep_trailing_newline=True)
_env.filters["yaml"] = _yaml_filter


def compile_template(source: str):
    try:
        return _env.from_string(source)
    except TemplateSyntaxError as exc:
        raise TemplateError(f"template line {exc.lineno}: {exc.message}") from exc


# ── field values ────────────────────────────────────────────


def default_values(template: Template) -> dict[str, Any]:
    """Raw form values (what widgets hold) for a fresh form."""
    return {f.key: to_raw(f, f.default) for f in template.fields}


def to_raw(f: TemplateField, value: Any) -> Any:
    """A typed value → what the form widget holds (str, or bool for checkboxes)."""
    if f.type == "bool":
        return bool(value)
    if value is None:
        return ""
    if f.type == "kv" and isinstance(value, dict):
        return format_kv_lines(value)
    if f.type == "list" and isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value)
    if f.type == "select":
        return value
    if isinstance(value, float) and value.is_integer() and f.type == "int":
        return str(int(value))
    return str(value)


def coerce(template: Template, raw: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """Raw form values → typed template variables, plus per-field errors."""
    values: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for f in template.fields:
        try:
            values[f.key] = _coerce_one(f, raw.get(f.key, to_raw(f, f.default)))
        except ValueError as exc:
            errors[f.key] = str(exc)
            values[f.key] = None
    return values, errors


def _coerce_one(f: TemplateField, raw: Any) -> Any:
    if f.key == "name":  # the resource name: same rules everywhere
        from twisk.specs.models import _validate_name  # noqa: PLC0415

        return _validate_name("" if raw is None else str(raw))
    if f.type == "bool":
        return bool(raw)
    if f.type == "select":
        allowed = [o.value for o in f.options]
        if raw in ("", None):
            if f.required:
                raise ValueError("required")
            return None if None in allowed or "" not in allowed else ""
        if raw not in allowed:
            matches = [v for v in allowed if str(v) == str(raw)]
            if not matches:
                raise ValueError(f"must be one of {', '.join(str(v) for v in allowed)}")
            raw = matches[0]
        return raw
    text = "" if raw is None else str(raw)
    if not text.strip():
        if f.required:
            raise ValueError("required")
        return None
    if f.type == "int":
        try:
            value: Any = int(text.strip())
        except ValueError:
            raise ValueError("must be a whole number") from None
        _bounds(f, value)
        return value
    if f.type == "float":
        try:
            value = float(text.strip())
        except ValueError:
            raise ValueError("must be a number") from None
        _bounds(f, value)
        return value
    if f.type == "kv":
        return parse_kv_lines(text)  # raises ValueError with a readable message
    if f.type == "list":
        return [p.strip() for p in text.split(",") if p.strip()]
    value = text if f.type == "area" else text.strip()
    if f.pattern and not re.fullmatch(f.pattern, value):
        raise ValueError(f.help or f"must match {f.pattern}")
    return value


def _bounds(f: TemplateField, value: float) -> None:
    if f.min is not None and value < f.min:
        raise ValueError(f"must be ≥ {f.min:g}")
    if f.max is not None and value > f.max:
        raise ValueError(f"must be ≤ {f.max:g}")


# ── rendering ───────────────────────────────────────────────


def render(template: Template, values: dict[str, Any], styles: dict[tuple, str] | None = None) -> dict[str, Any]:
    """Render the template with typed values → pruned YAML mapping.

    ``styles``, if given, receives the paths the template wrote as ``|``/``>`` blocks.
    """
    try:
        text = compile_template(template.template).render(**values)
    except TemplateError:
        raise
    except Exception as exc:  # noqa: BLE001 - jinja runtime errors
        raise TemplateError(f"template error: {exc}") from exc
    try:
        data = yaml.safe_load(text) if text.strip() else {}
    except yaml.YAMLError as exc:
        first = str(exc).splitlines()
        raise TemplateError(f"template rendered invalid YAML: {' '.join(first[:2])}") from exc
    if not isinstance(data, dict):
        raise TemplateError("template must render a YAML mapping")
    if styles is not None:
        styles.update(block_styles(text))
    return prune(data)


def prune(value: Any) -> Any:
    """Drop keys whose value is null, an empty string, or an empty list/mapping."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            v = prune(v)
            if v is None or v == "" or v == {} or v == []:
                continue
            out[k] = v
        return out
    if isinstance(value, list):
        return [prune(v) for v in value]
    return value


def merge3(ours: Any, base: Any, theirs: Any) -> Any:
    """Three-way merge of YAML data.

    ``ours`` is the file as it is now (maybe hand-edited), ``base`` what the
    form rendered last time, ``theirs`` what the form renders now. Keys the
    user changed by hand and the form didn't touch are kept; anything the form
    changes wins.
    """
    if isinstance(ours, dict) and isinstance(theirs, dict) and isinstance(base, (dict, type(_MISSING))):
        base = base if isinstance(base, dict) else {}
        out: dict[str, Any] = {}
        for k in list(dict.fromkeys([*ours, *theirs, *base])):
            o, b, t = ours.get(k, _MISSING), base.get(k, _MISSING), theirs.get(k, _MISSING)
            if isinstance(o, dict) and isinstance(t, dict):
                v = merge3(o, b if isinstance(b, dict) else _MISSING, t)
            elif o == b:
                v = t  # untouched by hand: follow the form
            elif t == b:
                v = o  # form unchanged here: keep the hand edit
            else:
                v = t  # both changed: the form wins
            if v is not _MISSING:
                out[k] = v
        return out
    if ours == base:
        return theirs
    if theirs == base:
        return ours
    return theirs


def normalize(kind: SpecKind, name: str, data: dict[str, Any]) -> dict[str, Any]:
    """Canonical YAML mapping for comparison (what twisk would save)."""
    spec = SPEC_TYPES[kind].from_yaml_dict(name, dict(data))
    out = spec.to_yaml_dict()
    out.pop("name", None)
    return out


# ── materialize / prefill ───────────────────────────────────


@dataclass
class Result:
    spec: Spec | None
    data: dict[str, Any]  # the YAML mapping the spec was built from
    values: dict[str, Any]  # typed values (for re-editing later)
    errors: dict[str, str] = field(default_factory=dict)


def materialize(
    template: Template,
    raw: dict[str, Any],
    *,
    base_data: dict[str, Any] | None = None,
    base_values: dict[str, Any] | None = None,
    base_styles: dict[tuple, str] | None = None,
) -> Result:
    """Raw form values → spec. ``base_*`` describe the spec being edited, if any
    (``base_styles``: where its YAML used ``|``/``>`` blocks, kept on re-save)."""
    values, errors = coerce(template, raw)
    if errors:
        # Still validate the rest, so every problem shows at once: render with
        # the broken fields blanked (and a stand-in name) and add spec errors
        # for the fields that did parse.
        trial = dict(values)
        if "name" in errors:
            trial["name"] = "x"
        probe = _build(template, trial, None, None, None)
        for key, msg in probe.errors.items():
            if key not in errors and key != "_form":
                errors[key] = msg
        return Result(None, {}, values, errors)
    return _build(template, values, base_data, base_values, base_styles)


def _build(
    template: Template,
    values: dict[str, Any],
    base_data: dict[str, Any] | None,
    base_values: dict[str, Any] | None,
    base_styles: dict[tuple, str] | None,
) -> Result:
    name = values.get("name") or ""
    styles: dict[tuple, str] = dict(base_styles or {})
    try:
        data = render(template, values, styles)
    except TemplateError as exc:
        return Result(None, {}, values, {"_form": str(exc)})
    data.pop("name", None)
    meta = {f.path[len("twisk."):]: values.get(f.key) for f in template.fields if f.is_meta}
    if base_data is not None and base_values is not None:
        try:
            ours = normalize(template.kind, name, base_data)
            base = normalize(template.kind, name, render(template, coerce(template, base_values)[0]))
            theirs = normalize(template.kind, name, data)
            data = prune(merge3(ours, base, theirs))
        except (TemplateError, ValidationError, ValueError, TypeError):
            pass  # fall back to the fresh render; validation below reports problems
    try:
        spec = SPEC_TYPES[template.kind].from_yaml_dict(name, dict(data))
        if isinstance(spec, JobSpec) and "pool" in meta:
            spec = JobSpec.model_validate({**spec.model_dump(), "pool": meta["pool"]})
        set_block_styles(spec, styles)
    except ValidationError as exc:
        return Result(None, data, values, map_errors(template, exc))
    except (ValueError, TypeError) as exc:
        return Result(None, data, values, {"_form": str(exc)})
    return Result(spec, data, values)


def map_errors(template: Template, exc: ValidationError) -> dict[str, str]:
    """Attach spec validation errors to the form fields that produce them."""
    errors: dict[str, str] = {}
    by_path = {alt.strip(): f.key for f in template.fields if f.path for alt in f.path.split("|")}
    for err in exc.errors():
        msg = str(err.get("msg", "invalid")).removeprefix("Value error, ")
        loc = [str(p) for p in err.get("loc", ()) if not isinstance(p, int)]
        path = ".".join(loc)
        # Model-level checks say which YAML path they're about: "run: ..."
        if not path and ":" in msg:
            head, rest = msg.split(":", 1)
            if re.fullmatch(r"[A-Za-z_][\w.]*", head.strip()):
                path, msg = head.strip(), rest.strip()
        key = _field_for_path(path, by_path, template)
        errors.setdefault(key or "_form", msg if key else (f"{path}: {msg}" if path else msg))
    return errors


def _field_for_path(path: str, by_path: dict[str, str], template: Template) -> str | None:
    if not path:
        return None
    if path in by_path:
        return by_path[path]
    for fp, key in by_path.items():  # resources.autostop → resources.autostop.idle_minutes
        if fp.startswith(path + ".") or path.startswith(fp + "."):
            return key
    leaf = path.split(".")[-1]
    for fp, key in by_path.items():
        if fp.split(".")[-1] == leaf:
            return key
    return leaf if template.field(leaf) else None


def prefill(template: Template, spec: Spec) -> dict[str, Any]:
    """Raw form values for an existing spec, read through the fields' ``path``."""
    data = spec.to_yaml_dict()
    raw: dict[str, Any] = {}
    for f in template.fields:
        if f.key == "name":
            raw[f.key] = spec.name
            continue
        if f.is_meta:
            value = getattr(spec, f.path[len("twisk."):], None)
        elif f.path:
            value = _lookup(data, f.path)
        else:
            value = f.default
        raw[f.key] = to_raw(f, value)
    return raw


def _lookup(data: Any, path: str) -> Any:
    """Dotted lookup; ``a.b|c.d`` tries alternatives in order."""
    for alt in path.split("|"):
        cur, found = data, True
        for part in alt.strip().split("."):
            if not isinstance(cur, dict) or part not in cur:
                found = False
                break
            cur = cur[part]
        if found:
            return cur
    return None


def values_to_raw(template: Template, values: dict[str, Any]) -> dict[str, Any]:
    """Typed values (as stored in a spec header) → raw form values."""
    return {f.key: to_raw(f, values.get(f.key, f.default)) for f in template.fields}
