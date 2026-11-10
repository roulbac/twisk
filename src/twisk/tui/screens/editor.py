"""In-TUI vim editor for a spec's YAML — the escape hatch for customizations.

Opened with ``E`` on any resource or spec, or ``ctrl+e`` from a create/edit
form (for a resource that doesn't exist yet). Nothing is written until ``:w``.

Commands: ``:w`` save · ``:wq`` / ``:x`` / ``ZZ`` save & close ·
``:apply`` save & launch/apply · ``:q`` close · ``:q!`` / ``ZQ`` discard ·
``:N`` go to line N · ``/pattern`` search (``n``/``N`` repeat).
``ctrl+s`` is a shortcut for ``:apply``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Static

from twisk.specs.models import Spec, SpecKind, spec_from_yaml
from twisk.specs.store import _short_error
from twisk.tui.widgets.vim import VimTextArea

SaveFn = Callable[[str], Spec]

_MODE_LABEL = {
    "normal": ("NORMAL", "black on #38bdf8"),
    "insert": ("INSERT", "black on #a3e635"),
    "visual": ("VISUAL", "black on #f59e0b"),
    "visual_line": ("V-LINE", "black on #f59e0b"),
}
HELP = ":w save · :wq save+close · :apply save+launch · :q close · :q! discard · i insert · v/V visual · u undo · /search"


@dataclass(frozen=True)
class EditorResult:
    spec: Spec | None  # last saved spec (None if never saved)
    saved: bool
    apply: bool  # user asked to launch/apply after saving


class YamlEditorScreen(Screen[EditorResult | None]):
    BINDINGS = [Binding("ctrl+s", "apply", "Save & apply", priority=True, show=False)]

    def __init__(self, kind: SpecKind, name: str, text: str, save: SaveFn, path: str | None = None,
                 is_new: bool = False, *, validate: Callable[[str], object] | None = None,
                 label: str | None = None) -> None:
        super().__init__()
        self.kind = kind
        self.spec_name = name
        # What's being edited: a spec (default) or something else, e.g. a form template.
        self._validate = validate or (lambda text: spec_from_yaml(kind, name, text))
        self.label = label or f"{kind.title.lower()} {name}"
        # Vim shows a file's final newline implicitly; keep it out of the buffer.
        self.initial = text.rstrip("\n")
        self.saved_text = None if is_new else self.initial
        self.path = path
        self.is_new = is_new
        self._save_fn = save
        self._saved_spec: Spec | None = None
        self._message: Text | None = None

    # ── layout ───────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        where = f"  [dim]{self.path}[/dim]" if self.path else ""
        new = "  [yellow](new — not saved yet)[/yellow]" if self.is_new else ""
        yield Static(f"[b #f59e0b] YAML · {self.label}[/]{where}{new}", id="editor-title")
        try:
            area = VimTextArea.code_editor(self.initial, language="yaml", id="editor", soft_wrap=False)
        except Exception:  # noqa: BLE001 - no tree-sitter grammar available
            area = VimTextArea.code_editor(self.initial, id="editor", soft_wrap=False)
        area.indent_width = 2
        yield area
        yield Static("", id="editor-status")

    def on_mount(self) -> None:
        self.editor.focus()
        self._message = Text(HELP, style="dim")
        self._refresh_status()

    @property
    def editor(self) -> VimTextArea:
        return self.query_one("#editor", VimTextArea)

    @property
    def modified(self) -> bool:
        return self.editor.text != self.saved_text

    # ── status line ─────────────────────────────────────────

    def _validation(self) -> tuple[Spec | None, str | None]:
        try:
            return self._validate(self.editor.text + "\n"), None
        except Exception as exc:  # noqa: BLE001
            return None, _short_error(exc)

    def _refresh_status(self) -> None:
        ed = self.editor
        if ed.cmdline_prefix is not None:  # typing a :command or /search
            line = Text(f"{ed.cmdline_prefix}{ed.cmdline_text}", style="bold")
            line.append("█", style="#38bdf8")
            self.query_one("#editor-status", Static).update(line)
            return
        label, style = _MODE_LABEL.get(ed.mode, (ed.mode.upper(), "reverse"))
        _, error = self._validation()
        row, col = ed.cursor_location
        line = Text()
        line.append(f" {label} ", style=f"bold {style}")  # style only the mode tag
        line.append(f" {self.spec_name}.yaml", style="bold")
        if self.modified:
            line.append(" [+]", style="yellow")
        line.append("   ")
        line.append("✓ valid" if error is None else f"✗ {error}", style="green" if error is None else "red")
        if self._message is not None and self._message.plain:
            line.append("   ")
            line.append(self._message)
        line.append(f"   {row + 1}:{col + 1}", style="dim")
        self.query_one("#editor-status", Static).update(line)

    def _say(self, text: str, error: bool = False) -> None:
        self._message = Text(text, style="bold red" if error else "")
        self._refresh_status()

    @on(VimTextArea.Changed)
    @on(VimTextArea.SelectionChanged)
    @on(VimTextArea.ModeChanged)
    def _changed(self, event) -> None:
        if isinstance(event, VimTextArea.ModeChanged) and event.mode == "insert":
            self._message = None
        self._refresh_status()

    @on(VimTextArea.Notice)
    def _notice(self, event: VimTextArea.Notice) -> None:
        self._say(event.text, event.error)

    # ── command line ────────────────────────────────────────

    @on(VimTextArea.CommandLineChanged)
    def _cmdline_changed(self, event: VimTextArea.CommandLineChanged) -> None:
        self._refresh_status()

    @on(VimTextArea.CommandSubmitted)
    def _command_submitted(self, event: VimTextArea.CommandSubmitted) -> None:
        self.run_command(event.command)

    def run_command(self, cmd: str) -> None:
        cmd = cmd.strip()
        if not cmd:
            return
        if cmd.isdigit():
            row = max(0, min(int(cmd) - 1, self.editor.document.line_count - 1))
            self.editor.move_cursor((row, self.editor._first_non_blank(row)))
            self.editor.scroll_cursor_visible()
            return
        if cmd in ("w", "write"):
            if self._write():
                self._say(f'"{self.spec_name}.yaml" written')
        elif cmd in ("wq", "x", "xit", "exit"):
            if not self.modified and self.saved_text is not None:
                self._close(apply=False)
            elif self._write():
                self._close(apply=False)
        elif cmd in ("apply", "launch", "wa", "run"):
            if self._write():
                self._close(apply=True)
        elif cmd in ("q", "quit"):
            if self.modified:
                self._say("E37: No write since last change (add ! to override)", error=True)
            else:
                self._close(apply=False)
        elif cmd in ("q!", "quit!", "cq"):
            self._close(apply=False)
        elif cmd in ("help", "h"):
            self._say(HELP)
        elif cmd in ("noh", "nohlsearch"):
            self._message = None
            self._refresh_status()
        else:
            self._say(f"E492: Not an editor command: {cmd}", error=True)

    def _write(self) -> bool:
        text = self.editor.text
        try:
            spec = self._save_fn(text + "\n")
        except Exception as exc:  # noqa: BLE001 - invalid YAML/spec: keep editing
            self._say(f"Not written: {_short_error(exc)}", error=True)
            return False
        self.saved_text = text
        self._saved_spec = spec
        return True

    def _close(self, apply: bool) -> None:
        if self._saved_spec is None:
            self.dismiss(None)
        else:
            self.dismiss(EditorResult(self._saved_spec, saved=True, apply=apply))

    def action_apply(self) -> None:
        self.run_command("apply")
