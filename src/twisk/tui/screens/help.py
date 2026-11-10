"""Help overlay listing global and per-view keys."""

from __future__ import annotations

from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

from twisk.tui.views import TOP_VIEWS, View

GLOBAL_KEYS = [
    (":", "command mode — :clusters :jobs :services :pools :volumes :storage :usage :specs :requests :infra :ws :templates :q"),
    ("1-9, 0", "jump to a view"),
    ("j / k, g / G", "move down / up, to top / bottom (arrows work too)"),
    ("U", "toggle scope: only my resources / every user's"),
    ("/", "filter rows (esc clears)"),
    ("ctrl-r", "refresh now (clusters: query clouds for fresh status)"),
    ("< / >", "sort by previous / next column; again to reverse"),
    ("esc", "back / clear filter"),
    ("q", "back; quits at the top level"),
    ("?", "this help"),
    ("ctrl-c", "quit"),
]


class HelpScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape,q,question_mark", "close", "Close", show=False)]

    def __init__(self, view: View) -> None:
        super().__init__()
        self.view = view

    def compose(self) -> ComposeResult:
        t = Table.grid(padding=(0, 3))
        t.add_column(style="bold #38bdf8", no_wrap=True)
        t.add_column()
        t.add_row(Text("Global", style="bold #f59e0b"), "")
        for k, d in GLOBAL_KEYS:
            t.add_row(k, d)
        t.add_row("", "")
        t.add_row(Text(f"{self.view.title}", style="bold #f59e0b"), "")
        for a in self.view.actions:
            t.add_row(Text(a.display_key, style="bold red" if a.danger else "bold #38bdf8"), a.label)
        t.add_row("", "")
        t.add_row(Text("Forms", style="bold #f59e0b"), "")
        t.add_row("ctrl-s", "save the spec and launch / apply it")
        t.add_row("ctrl-o", "save the spec only")
        t.add_row("ctrl-e", "continue in the vim YAML editor (nothing is written until :w)")
        t.add_row("", "")
        t.add_row(Text("YAML editor (vim)", style="bold #f59e0b"), "E on any resource/spec, ctrl-e in a form")
        t.add_row(":w  :wq  :q!", "save · save & close · discard and close")
        t.add_row(":apply  ctrl-s", "save, then launch / apply / roll out")
        t.add_row("i a o O  esc", "insert · append · open line (YAML-indented) · back to normal")
        t.add_row("h j k l w b e 0 ^ $ gg G", "motions (counts work: 3j, 2w)")
        t.add_row("d c y > < + motion", "operators; dd yy cc >> << for lines; x D C J r p P")
        t.add_row("v V  u ctrl-r  /  n N", "visual · undo/redo · search")
        t.add_row("", "")
        t.add_row(Text("Views", style="bold #f59e0b"), "")
        for v in TOP_VIEWS:
            t.add_row(f"{v.hotkey or ' '}  :{v.name}", f"{v.title}  [dim]({', '.join(a for a in v.aliases if a != v.name)})[/dim]")
        t.add_row("", "")
        t.add_row(Text("Templates", style="bold #f59e0b"), "Forms are templates: ~/.twisk/templates/<name>.yaml (form fields +")
        t.add_row("", "a Jinja2 YAML template). :templates lists them; E edits, c creates, D copies.")
        t.add_row("", "A file named after a kind (cluster.yaml…) replaces that built-in form.")
        t.add_row("", "")
        t.add_row(Text("Specs", style="bold #f59e0b"), "Everything you create is saved as plain SkyPilot YAML in")
        t.add_row("", "~/.twisk/specs/<kind>/<name>.yaml — usable directly with the sky CLI.")
        t.add_row("", "E opens it in the built-in vim editor; e re-opens the form.")
        with VerticalScroll(classes="help-box"):
            yield Static(t)
            yield Static("[dim]esc to close[/dim]")

    def action_close(self) -> None:
        self.dismiss()
