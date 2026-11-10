"""Create/edit forms, generated from a template, with a live YAML preview.

Every form is a template (built-in or from ``~/.twisk/templates``). Ctrl+S
saves the spec to the library *and* launches/applies it; Ctrl+O only saves it.
Ctrl+E continues in the vim YAML editor (escape hatch).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.suggester import SuggestFromList
from textual.widgets import Button, Footer, Input, Label, Select, Static, Switch, TextArea

from twisk.specs.models import Spec, SpecKind
from twisk.templates import Template, TemplateField, default_values, materialize
from twisk.tui.screens.detail import yaml_block

ACCELERATORS = [
    "H100:1", "H100:8", "H200:8", "B200:8", "A100:1", "A100:8", "A100-80GB:1", "A100-80GB:8",
    "L40S:1", "L4:1", "L4:4", "A10G:1", "A10:1", "T4:1", "V100:1", "RTX4090:1", "MI300X:8", "tpu-v5e-8",
]
INFRA_HINTS = ["k8s", "aws", "gcp", "azure", "lambda", "nebius", "runpod", "kubernetes", "ssh"]
LAUNCH_LABEL = {
    SpecKind.CLUSTER: "Launch", SpecKind.JOB: "Submit", SpecKind.SERVICE: "Deploy",
    SpecKind.VOLUME: "Apply", SpecKind.POOL: "Apply",
}


@dataclass(frozen=True)
class FormResult:
    spec: Spec
    launch: bool
    raw: bool = False  # user asked to continue in the vim YAML editor
    template: str | None = None
    values: dict[str, Any] | None = None  # typed values, recorded in the spec header


class FormScreen(Screen[FormResult | None]):
    BINDINGS = [
        Binding("ctrl+s", "submit(True)", "Save & launch", priority=True),
        Binding("ctrl+o", "submit(False)", "Save only", priority=True),
        Binding("ctrl+e", "raw", "Edit as YAML", priority=True),
        Binding("escape", "cancel", "Cancel", priority=True),
    ]

    def __init__(
        self,
        template: Template,
        values: dict[str, Any] | None = None,
        *,
        editing: bool = False,
        base_data: dict[str, Any] | None = None,
        base_values: dict[str, Any] | None = None,
        base_styles: dict[tuple, str] | None = None,
        launch_label: str | None = None,
        suggestions: dict[str, list[str]] | None = None,
    ) -> None:
        super().__init__()
        self.template = template
        self.kind = template.kind
        self.editing = editing
        self.values: dict[str, Any] = values if values is not None else default_values(template)
        # When editing: the file as it is and the values it was last rendered
        # from, for a three-way merge that keeps hand edits made in YAML.
        self.base_data = base_data
        self.base_values = base_values
        self.base_styles = base_styles
        self.launch_label = launch_label or LAUNCH_LABEL[template.kind]
        self.suggestions = suggestions or {}
        self._show_errors = False  # after the first submit, keep errors live
        self._select_values: dict[str, list[Any]] = {}

    # ── layout ───────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        verb = "Edit" if self.editing else "New"
        custom = "" if self.template.name == self.kind.value else f" · [b]{self.template.display_title}[/b]"
        origin = "built-in" if self.template.source == "builtin" else "yours"
        source = f"  [dim]template: {self.template.name} ({origin})[/dim]"
        yield Static(f"[b #f59e0b] {verb} {self.kind.title.lower()}[/]{custom}{source}", id="form-title")
        with Horizontal(id="form-body"):
            with VerticalScroll(id="form-fields"):
                if self.template.description and self.template.name != self.kind.value:
                    yield Static(f"[dim]{self.template.description}[/dim]", classes="field-help")
                for section in self.template.sections():
                    yield Static(f"[b]{section}[/b]", classes="form-section")
                    for f in self.template.fields:
                        if f.section == section:
                            yield from self._field(f)
                yield Static("", id="e-_form", classes="field-error")
            with Vertical(id="form-preview"):
                yield Static("[b]YAML preview[/b] [dim]· saved to the spec library[/dim]", classes="form-section")
                with VerticalScroll():
                    yield Static("", id="preview")
        with Horizontal(id="form-buttons"):
            yield Button(f"{self.launch_label} (ctrl+s)", id="launch", variant="primary")
            yield Button("Save only (ctrl+o)", id="save")
            yield Button("Edit as YAML (ctrl+e)", id="raw")
            yield Button("Cancel (esc)", id="cancel")
        yield Footer()

    def _suggestions_for(self, f: TemplateField) -> list[str]:
        if isinstance(f.suggest, list):
            return [str(s) for s in f.suggest]
        if f.suggest == "accelerators":
            return list(ACCELERATORS)
        if f.suggest == "infra":
            dynamic = self.suggestions.get("infra", [])
            return dynamic + [s for s in INFRA_HINTS if s not in dynamic]
        if f.suggest in ("volumes", "pools"):
            return list(self.suggestions.get(f.suggest, []))
        return []

    def _field(self, f: TemplateField):
        value = self.values.get(f.key, False if f.type == "bool" else "")
        label = f.title + (" *" if f.required else "")
        wid = f"f-{f.key}"
        help_text = f.help
        if f.suggest in ("volumes", "pools") and self.suggestions.get(f.suggest):
            help_text += f" · existing: {', '.join(self.suggestions[f.suggest][:6])}"
        if f.type in ("area", "kv"):
            yield Label(label + (f"  [dim]{help_text}[/dim]" if help_text else ""), classes="field-label")
            yield TextArea(str(value or ""), id=wid, classes="field-area", soft_wrap=True, show_line_numbers=False)
        else:
            with Horizontal(classes="field-row"):
                yield Label(label, classes="field-name")
                if f.type == "bool":
                    yield Switch(value=bool(value), id=wid)
                elif f.type == "select":
                    yield self._select(f, value, wid)
                else:
                    suggestions = self._suggestions_for(f)
                    yield Input(
                        str(value if value is not None else ""), placeholder=f.placeholder, id=wid, compact=True,
                        valid_empty=True, type={"int": "integer", "float": "number"}.get(f.type, "text"),
                        suggester=SuggestFromList(suggestions, case_sensitive=False) if suggestions else None,
                    )
            if help_text:
                yield Static(f"[dim]{help_text}[/dim]", classes="field-help")
        yield Static("", id=f"e-{f.key}", classes="field-error")

    @staticmethod
    def _option_id(value: Any) -> str:
        # Select values must be non-empty; "" / None (= leave it to SkyPilot) map to a sentinel.
        return "__default__" if value in ("", None) else str(value)

    def _select(self, f: TemplateField, value: Any, wid: str) -> Select:
        options = [(o.label, o.value) for o in f.options]
        if value not in [v for _, v in options] and value not in ("", None):  # set by hand in the YAML
            options.append((str(value), value))
        self._select_values[f.key] = [v for _, v in options]
        ids = [self._option_id(v) for _, v in options]
        current = self._option_id(value)
        if current not in ids:
            current = ids[0]
        return Select([(lbl, oid) for (lbl, _), oid in zip(options, ids)], value=current, id=wid,
                      allow_blank=False, compact=True)

    def on_mount(self) -> None:
        if self.editing:
            # Renaming would create a different resource; keep the name fixed.
            self.query_one("#f-name").disabled = True
            nxt = next((f for f in self.template.fields if f.key != "name" and f.type not in ("bool", "select")), None)
            if nxt is not None:
                self.query_one(f"#f-{nxt.key}").focus()
        else:
            self.query_one("#f-name").focus()
        self._refresh_preview()

    # ── values & preview ─────────────────────────────────────

    def collect(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for f in self.template.fields:
            w = self.query_one(f"#f-{f.key}")
            if isinstance(w, Switch):
                out[f.key] = w.value
            elif isinstance(w, TextArea):
                out[f.key] = w.text
            elif isinstance(w, Select):
                choice = w.value
                options = self._select_values.get(f.key, [])
                out[f.key] = next((v for v in options if self._option_id(v) == choice), "")
            elif isinstance(w, Input):
                out[f.key] = w.value
        return out

    def _build(self):
        return materialize(self.template, self.collect(), base_data=self.base_data, base_values=self.base_values,
                           base_styles=self.base_styles)

    def _refresh_preview(self, show_errors: bool = False):
        self._show_errors = self._show_errors or show_errors
        show_errors = self._show_errors
        result = self._build()
        preview = self.query_one("#preview", Static)
        if result.spec is not None:
            preview.update(yaml_block(result.spec.to_yaml()))
        else:
            lines = "\n".join(f"#  {k if k != '_form' else 'form'}: {v}" for k, v in result.errors.items())
            preview.update(yaml_block(f"# fix these to see the YAML:\n{lines}"))
        for key in [f.key for f in self.template.fields] + ["_form"]:
            box = self.query_one(f"#e-{key}", Static)
            show = show_errors and key in result.errors
            box.update(f"✗ {result.errors[key]}" if show else "")
            box.display = show
        return result

    @on(Input.Changed)
    @on(Switch.Changed)
    @on(Select.Changed)
    @on(TextArea.Changed)
    def _changed(self, event) -> None:
        self._refresh_preview()

    # ── actions ──────────────────────────────────────────────

    def _result(self, launch: bool, raw: bool = False) -> FormResult | None:
        result = self._refresh_preview(show_errors=True)
        if result.spec is None:
            self.notify("Fix the highlighted fields first", severity="error")
            return None
        return FormResult(result.spec, launch=launch, raw=raw, template=self.template.name, values=result.values)

    def action_submit(self, launch: bool) -> None:
        result = self._result(launch)
        if result is not None:
            self.dismiss(result)

    def action_raw(self) -> None:
        result = self._result(False, raw=True)
        if result is not None:
            self.dismiss(result)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "launch":
            self.action_submit(True)
        elif bid == "save":
            self.action_submit(False)
        elif bid == "raw":
            self.action_raw()
        else:
            self.action_cancel()
