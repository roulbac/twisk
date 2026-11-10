"""Behaviour of the vim-mode TextArea (keys fed through VimTextArea.handle)."""

from __future__ import annotations

import pytest
from textual.app import App

from twisk.tui.widgets.vim import VimTextArea

YAML = "name: dev\nresources:\n  cpus: 4\n  memory: 16\nrun: echo hi"


class _App(App):
    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text
        self.commands: list[str] = []

    def on_vim_text_area_command_submitted(self, message: VimTextArea.CommandSubmitted) -> None:
        self.commands.append(message.command)

    def compose(self):
        yield VimTextArea(self.text, id="v")

    def on_mount(self) -> None:
        self.query_one(VimTextArea).focus()


@pytest.fixture
async def vim():
    app = _App(YAML)
    async with app.run_test() as pilot:
        area = app.query_one(VimTextArea)

        async def keys(seq, *, typed: str | None = None):
            for tok in seq:
                area.handle(tok)
            if typed is not None:  # insert-mode text, then <esc>
                for ch in typed:
                    await pilot.press("space" if ch == " " else ch)
                await pilot.press("escape")
            await pilot.pause()

        area.keys = keys
        yield area


def lines(area):
    return area.text.split("\n")


async def test_starts_in_normal_mode_and_ignores_typing(vim):
    assert vim.mode == "normal"
    await vim.keys(["z", "q"])  # unbound keys do nothing
    assert vim.text == YAML


async def test_motions(vim):
    await vim.keys(["j", "j"])
    assert vim.cursor_location == (2, 0)
    await vim.keys(["^"])
    assert vim.cursor_location == (2, 2)
    await vim.keys(["w"])
    assert vim.cursor_location == (2, 6)  # ':' is its own word
    await vim.keys(["$"])
    assert vim.cursor_location == (2, 8)
    await vim.keys(["G"])
    assert vim.cursor_location[0] == 4
    await vim.keys(["g", "g"])
    assert vim.cursor_location == (0, 0)
    await vim.keys(["3", "G"])
    assert vim.cursor_location[0] == 2
    await vim.keys(["b"])
    assert vim.cursor_location == (1, 9)


async def test_change_word_and_undo_redo_as_one_change(vim):
    await vim.keys(["j", "j", "^", "c", "w"], typed="cores")
    assert lines(vim)[2] == "  cores: 4"
    await vim.keys(["u"])
    assert lines(vim)[2] == "  cpus: 4"
    await vim.keys(["ctrl+r"])
    assert lines(vim)[2] == "  cores: 4"


async def test_delete_yank_put_lines(vim):
    await vim.keys(["d", "d"])
    assert lines(vim)[0] == "resources:" and vim.register.linewise
    await vim.keys(["G", "p"])
    assert lines(vim)[-1] == "name: dev"
    await vim.keys(["g", "g", "y", "y", "P"])
    assert lines(vim)[:2] == ["resources:", "resources:"]
    await vim.keys(["2", "d", "d"])
    assert lines(vim)[0] == "  cpus: 4"


async def test_operator_with_count_and_motion(vim):
    await vim.keys(["d", "2", "j"])  # deletes 3 lines
    assert lines(vim)[0] == "  memory: 16"
    await vim.keys(["d", "G"])
    assert vim.text == ""


async def test_char_edits(vim):
    await vim.keys(["x"])
    assert lines(vim)[0] == "ame: dev"
    await vim.keys(["r", "N"])
    assert lines(vim)[0] == "Nme: dev"
    await vim.keys(["$", "D"])
    assert lines(vim)[0] == "Nme: de"
    await vim.keys(["j", "J"])
    assert lines(vim)[1] == "resources: cpus: 4"


async def test_open_line_keeps_yaml_indentation(vim):
    await vim.keys(["j", "o"], typed="gpus: 1")  # under "resources:" → indented
    assert lines(vim)[2] == "  gpus: 1"
    await vim.keys(["o"], typed="disk: 50")  # sibling keeps indentation
    assert lines(vim)[3] == "  disk: 50"
    await vim.keys(["g", "g", "O"], typed="# top")
    assert lines(vim)[0] == "# top"


async def test_insert_variants(vim):
    await vim.keys(["A"], typed="-box")
    assert lines(vim)[0] == "name: dev-box"
    await vim.keys(["I"], typed="#")
    assert lines(vim)[0] == "#name: dev-box"
    await vim.keys(["0", "x", "a"], typed="N")
    assert lines(vim)[0] == "nName: dev-box"


async def test_visual_line_indent_and_delete(vim):
    await vim.keys(["j", "j", "V", "j", "<"])
    assert lines(vim)[2:4] == ["cpus: 4", "memory: 16"]
    await vim.keys(["V", "j", ">"])
    assert lines(vim)[2:4] == ["  cpus: 4", "  memory: 16"]
    assert vim.mode == "normal"
    await vim.keys(["V", "d"])
    assert "  cpus: 4" not in lines(vim)


async def test_visual_char_yank_and_escape(vim):
    await vim.keys(["w", "w", "v", "e", "y"])
    assert vim.register.text == "dev" and vim.mode == "normal"
    await vim.keys(["v", "l", "escape"])
    assert vim.mode == "normal"


async def test_search_and_repeat(vim):
    assert vim.search("echo")
    assert vim.cursor_location == (4, 5)
    vim.search("cpus|memory")
    assert vim.cursor_location == (2, 2)  # wraps to top
    await vim.keys(["n"])
    assert vim.cursor_location == (3, 2)
    await vim.keys(["N"])
    assert vim.cursor_location == (2, 2)
    assert not vim.search("nope")


async def test_command_line_and_zz_submit_commands(vim):
    await vim.keys([":", "w", "q", "enter", "Z", "Z", "Z", "Q"])
    assert vim.app.commands == ["wq", "wq", "q!"]
    await vim.keys([":", "x", "escape"])  # cancelled: nothing submitted
    assert vim.app.commands == ["wq", "wq", "q!"] and vim.cmdline_prefix is None


async def test_search_from_command_line(vim):
    await vim.keys(["/", "m", "e", "m", "enter"])
    assert vim.cursor_location == (3, 2)
