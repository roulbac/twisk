"""A vim-mode editor widget: Textual's TextArea with modal editing.

Covers the vim most people use day to day — enough to tweak a YAML file
without leaving twisk:

* modes: normal, insert, visual (``v``), visual line (``V``)
* motions: ``h j k l w b e 0 ^ $ gg G``, arrows, ``ctrl+d/u/f/b``, counts (``3j``)
* operators: ``d c y > <`` with any motion, doubled for lines (``dd yy cc >> <<``)
* edits: ``x X D C Y s S J r p P o O i a I A u ctrl+r``
* search: ``/pattern`` then ``n N``
* a command line (``:`` and ``/``) handled inside the widget, so fast typing
  like ``:wq<enter>`` never races a focus change; ``:`` commands are handed to
  the screen via :class:`VimTextArea.CommandSubmitted`

Not supported: registers other than the unnamed one, marks, macros, ``.``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from textual import events
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import TextArea
from textual.widgets.text_area import Selection

Location = tuple[int, int]

_WORD = re.compile(r"\w")
# Keys left to TextArea in normal/visual mode (we map them ourselves in visual).
_ARROWS = {"up": "k", "down": "j", "left": "h", "right": "l"}
_OPERATORS = {"d", "c", "y", ">", "<"}
# Normal-mode commands that change the buffer (snapshot for undo first).
_MODIFYING = {"i", "a", "I", "A", "o", "O", "x", "X", "s", "S", "D", "C", "p", "P", "J", "~"}


def _char_class(ch: str) -> int:
    if ch.isspace():
        return 0
    return 1 if _WORD.match(ch) else 2


@dataclass
class _Register:
    text: str = ""
    linewise: bool = False


class VimTextArea(TextArea):
    """TextArea with vim keybindings. Starts in normal mode."""

    mode: reactive[str] = reactive("normal")

    class ModeChanged(Message):
        def __init__(self, area: "VimTextArea", mode: str) -> None:
            super().__init__()
            self.area = area
            self.mode = mode

    class CommandSubmitted(Message):
        """A ``:`` command was entered (``ZZ``/``ZQ`` submit ``wq``/``q!``)."""

        def __init__(self, area: "VimTextArea", command: str) -> None:
            super().__init__()
            self.area = area
            self.command = command

    class CommandLineChanged(Message):
        """The command line opened, changed or closed (``prefix`` None = closed)."""

        def __init__(self, area: "VimTextArea", prefix: str | None, text: str) -> None:
            super().__init__()
            self.area = area
            self.prefix = prefix
            self.text = text

    class Notice(Message):
        """A short message for the status line (e.g. 'Pattern not found')."""

        def __init__(self, area: "VimTextArea", text: str, error: bool = False) -> None:
            super().__init__()
            self.area = area
            self.text = text
            self.error = error

    UNDO_LIMIT = 500

    def __init__(self, text: str = "", **kwargs) -> None:
        super().__init__(text, **kwargs)
        self.indent_width = 2  # YAML
        self.register = _Register()
        # Snapshot undo: one entry per vim change (an operator, or an insert
        # session), which Textual's own edit batching can't express.
        self._undo: list[tuple[str, Location]] = []
        self._redo: list[tuple[str, Location]] = []
        self._count = ""
        self._pending = ""  # operator or prefix key waiting for more input
        self._pending_count = 1
        self._visual_anchor: Location | None = None
        self.last_search: str | None = None
        self._search_forward = True
        self.cmdline_prefix: str | None = None  # ":" / "/" / "?" while typing a command
        self.cmdline_text = ""

    # ── helpers ─────────────────────────────────────────────

    @property
    def lines(self) -> list[str]:
        return self.text.split("\n")

    def _line(self, row: int) -> str:
        return self.document.get_line(row)

    @property
    def _last_row(self) -> int:
        return self.document.line_count - 1

    def _clamp(self, loc: Location, allow_eol: bool = False) -> Location:
        row = max(0, min(loc[0], self._last_row))
        length = len(self._line(row))
        limit = length if allow_eol else max(length - 1, 0)
        return row, max(0, min(loc[1], limit))

    def _goto(self, loc: Location, allow_eol: bool = False) -> None:
        self.move_cursor(self._clamp(loc, allow_eol))
        self.scroll_cursor_visible()

    def _checkpoint(self) -> None:
        """Snapshot the buffer before a change so `u` restores it in one step."""
        snap = (self.text, self.cursor_location)
        if self._undo and self._undo[-1][0] == snap[0]:
            self._undo[-1] = snap  # nothing changed since the last snapshot
        else:
            self._undo.append(snap)
            del self._undo[: -self.UNDO_LIMIT]
        self._redo.clear()

    def _restore(self, snap: tuple[str, Location]) -> None:
        text, loc = snap
        self.replace(text, (0, 0), self.document.end)
        self._goto(loc)

    def vim_undo(self) -> bool:
        while self._undo:
            snap = self._undo.pop()
            if snap[0] != self.text:
                self._redo.append((self.text, self.cursor_location))
                self._restore(snap)
                return True
        self._notice("Already at oldest change")
        return False

    def vim_redo(self) -> bool:
        if not self._redo:
            self._notice("Already at newest change")
            return False
        snap = self._redo.pop()
        self._undo.append((self.text, self.cursor_location))
        self._restore(snap)
        return True

    def set_mode(self, mode: str) -> None:
        if mode == self.mode:
            return
        self.mode = mode
        if mode in ("normal", "insert"):
            self._visual_anchor = None
            if mode == "normal":
                loc = self.cursor_location
                self.selection = Selection.cursor(self._clamp(loc))
        self.post_message(self.ModeChanged(self, mode))

    def _notice(self, text: str, error: bool = False) -> None:
        self.post_message(self.Notice(self, text, error))

    # ── key handling ────────────────────────────────────────

    async def _on_key(self, event: events.Key) -> None:
        if self.read_only:
            return
        if self.mode == "insert":
            if event.key in ("escape", "ctrl+left_square_bracket", "ctrl+c"):
                event.stop()
                event.prevent_default()
                row, col = self.cursor_location
                self.set_mode("normal")
                self._goto((row, col - 1))
                return
            await super()._on_key(event)
            return
        if self.cmdline_prefix is not None:
            event.stop()
            event.prevent_default()
            self._cmdline_key(event)
            return
        # normal / visual: we own every key except page/home/end navigation.
        if event.key in ("pageup", "pagedown", "home", "end") and self.mode == "normal":
            return
        event.stop()
        event.prevent_default()
        token = _ARROWS.get(event.key) or (event.character if event.is_printable and event.character else event.key)
        try:
            self.handle(token)
        except Exception as exc:  # never let an editing bug kill the TUI
            self._reset_pending()
            self._notice(f"error: {exc}", error=True)

    # ── command line ────────────────────────────────────────

    def open_cmdline(self, prefix: str) -> None:
        self.cmdline_prefix, self.cmdline_text = prefix, ""
        self.post_message(self.CommandLineChanged(self, prefix, ""))

    def _close_cmdline(self) -> tuple[str, str]:
        prefix, text = self.cmdline_prefix or "", self.cmdline_text
        self.cmdline_prefix, self.cmdline_text = None, ""
        self.post_message(self.CommandLineChanged(self, None, ""))
        return prefix, text

    def _cmdline_key(self, event: events.Key) -> None:
        key = event.key
        if key in ("escape", "ctrl+c", "ctrl+left_square_bracket"):
            self._close_cmdline()
        elif key in ("enter", "ctrl+m", "ctrl+j"):
            self.submit_cmdline()
        elif key in ("backspace", "ctrl+h"):
            if not self.cmdline_text:
                self._close_cmdline()
                return
            self.cmdline_text = self.cmdline_text[:-1]
            self.post_message(self.CommandLineChanged(self, self.cmdline_prefix, self.cmdline_text))
        elif key == "ctrl+u":
            self.cmdline_text = ""
            self.post_message(self.CommandLineChanged(self, self.cmdline_prefix, ""))
        elif event.is_printable and event.character:
            self.cmdline_text += event.character
            self.post_message(self.CommandLineChanged(self, self.cmdline_prefix, self.cmdline_text))

    def submit_cmdline(self) -> None:
        prefix, text = self._close_cmdline()
        if prefix == ":":
            if text.strip():
                self.post_message(self.CommandSubmitted(self, text.strip()))
        elif prefix in ("/", "?"):
            self.search(text, forward=prefix == "/")

    def handle(self, tok: str) -> None:
        """Process one normal/visual-mode keystroke (public for tests)."""
        if self.cmdline_prefix is not None:
            if tok in ("escape", "ctrl+c"):
                self._close_cmdline()
            elif tok == "enter":
                self.submit_cmdline()
            elif tok == "backspace":
                self.cmdline_text = self.cmdline_text[:-1]
            else:
                self.cmdline_text += " " if tok == "space" else tok
            return
        if tok in ("escape", "ctrl+c", "ctrl+left_square_bracket"):
            self._reset_pending()
            if self.mode in ("visual", "visual_line"):
                self.set_mode("normal")
            return

        # Two-key prefixes waiting for their second key.
        if self._pending == "r":
            self._reset_pending()
            if len(tok) == 1:
                self._replace_char(tok)
            return
        if self._pending == "g":
            pending_count = self._pending_count
            self._pending = ""
            if tok == "g":
                self._apply_motion("gg", pending_count, explicit=bool(self._count))
            self._count = ""
            return
        if self._pending == "Z":
            self._reset_pending()
            if tok == "Z":
                self.post_message(self.CommandSubmitted(self, "wq"))
            elif tok == "Q":
                self.post_message(self.CommandSubmitted(self, "q!"))
            return

        if tok.isdigit() and (tok != "0" or self._count):
            self._count += tok
            return
        count = int(self._count) if self._count else 1
        explicit = bool(self._count)

        # Operator pending: this key is the motion (or the doubled operator).
        if self._pending in _OPERATORS:
            op, op_count = self._pending, self._pending_count
            self._count = ""
            self._pending = ""
            total = op_count * count
            if tok == op:
                self._operate_lines(op, total)
            elif tok == "g":
                self._pending = op + "g"
                self._pending_count = total
            else:
                self._operate_motion(op, tok, total, explicit)
            return
        if self._pending.endswith("g") and len(self._pending) == 2:
            op, total = self._pending[0], self._pending_count
            self._reset_pending()
            if tok == "g":
                self._operate_motion(op, "gg", total, explicit)
            return

        self._count = ""
        if self.mode in ("visual", "visual_line"):
            self._visual(tok, count, explicit)
            return
        self._normal(tok, count, explicit)

    def _reset_pending(self) -> None:
        self._pending = ""
        self._count = ""
        self._pending_count = 1

    # ── normal mode ─────────────────────────────────────────

    def _normal(self, tok: str, count: int, explicit: bool) -> None:
        row, col = self.cursor_location
        if self._is_motion(tok):
            self._apply_motion(tok, count, explicit)
            return
        if tok in _OPERATORS:
            self._pending, self._pending_count = tok, count
            return
        if tok == "g":
            self._pending, self._pending_count = "g", count
            self._count = str(count) if explicit else ""
            return
        if tok in ("r", "Z"):
            self._pending = tok
            return
        if tok in _MODIFYING:
            self._checkpoint()
        if tok == "i":
            self.set_mode("insert")
        elif tok == "a":
            self.set_mode("insert")
            self._goto((row, col + (1 if self._line(row) else 0)), allow_eol=True)
        elif tok == "I":
            self.set_mode("insert")
            self._goto((row, self._first_non_blank(row)), allow_eol=True)
        elif tok == "A":
            self.set_mode("insert")
            self._goto((row, len(self._line(row))), allow_eol=True)
        elif tok in ("o", "O"):
            self._open_line(below=tok == "o")
        elif tok == "x":
            self._operate_motion("d", "l", count, explicit)
        elif tok == "X":
            if col > 0:
                self._operate_motion("d", "h", count, explicit)
        elif tok == "s":
            self._operate_motion("c", "l", count, explicit)
        elif tok == "S":
            self._operate_lines("c", count)
        elif tok == "D":
            self._operate_motion("d", "$", count, explicit)
        elif tok == "C":
            self._operate_motion("c", "$", count, explicit)
        elif tok == "Y":
            self._operate_lines("y", count)
        elif tok == "p":
            self._put(after=True, count=count)
        elif tok == "P":
            self._put(after=False, count=count)
        elif tok == "J":
            self._join(max(count, 2) - 1)
        elif tok == "u":
            for _ in range(count):
                if not self.vim_undo():
                    break
        elif tok == "ctrl+r":
            for _ in range(count):
                if not self.vim_redo():
                    break
        elif tok == "v":
            self._visual_anchor = (row, col)
            self.set_mode("visual")
            self._update_visual()
        elif tok == "V":
            self._visual_anchor = (row, col)
            self.set_mode("visual_line")
            self._update_visual()
        elif tok in (":", "/", "?"):
            self.open_cmdline(tok)
        elif tok in ("n", "N"):
            self.search_next(reverse=tok == "N")
        elif tok == "~":
            self._toggle_case(count)

    # ── motions ─────────────────────────────────────────────

    _MOTIONS = {"h", "j", "k", "l", "w", "b", "e", "W", "B", "E", "0", "^", "$", "G", "gg", "enter",
                "ctrl+d", "ctrl+u", "ctrl+f", "ctrl+b", "backspace", "space", " ", "-", "+"}

    def _is_motion(self, tok: str) -> bool:
        return tok in self._MOTIONS

    def motion_target(self, tok: str, count: int, explicit: bool = False) -> tuple[Location, bool, bool]:
        """Return ``(target, linewise, inclusive)`` for a motion from the cursor."""
        row, col = self.cursor_location
        half = max(1, (self.scrollable_content_region.height or 20) // 2)
        if tok in ("h", "backspace"):
            return (row, max(0, col - count)), False, False
        if tok in ("l", "space", " "):
            return (row, min(len(self._line(row)), col + count)), False, False
        if tok in ("j", "+", "enter"):
            target = (min(self._last_row, row + count), col)
            if tok != "j":
                target = (target[0], self._first_non_blank(target[0]))
            return target, True, False
        if tok in ("k", "-"):
            target = (max(0, row - count), col)
            if tok == "-":
                target = (target[0], self._first_non_blank(target[0]))
            return target, True, False
        if tok in ("ctrl+d", "ctrl+f"):
            step = half if tok == "ctrl+d" else half * 2
            return (min(self._last_row, row + step * count), col), True, False
        if tok in ("ctrl+u", "ctrl+b"):
            step = half if tok == "ctrl+u" else half * 2
            return (max(0, row - step * count), col), True, False
        if tok == "0":
            return (row, 0), False, False
        if tok == "^":
            return (row, self._first_non_blank(row)), False, False
        if tok == "$":
            target_row = min(self._last_row, row + count - 1)
            return (target_row, max(len(self._line(target_row)) - 1, 0)), False, True
        if tok == "G":
            target_row = (count - 1) if explicit else self._last_row
            target_row = max(0, min(target_row, self._last_row))
            return (target_row, self._first_non_blank(target_row)), True, False
        if tok == "gg":
            target_row = (count - 1) if explicit else 0
            target_row = max(0, min(target_row, self._last_row))
            return (target_row, self._first_non_blank(target_row)), True, False
        loc = (row, col)
        if tok in ("w", "W"):
            for _ in range(count):
                loc = self._word_forward(loc, big=tok == "W")
            return loc, False, False
        if tok in ("b", "B"):
            for _ in range(count):
                loc = self._word_backward(loc, big=tok == "B")
            return loc, False, False
        if tok in ("e", "E"):
            for _ in range(count):
                loc = self._word_end(loc, big=tok == "E")
            return loc, False, True
        raise ValueError(f"unknown motion {tok!r}")

    def _apply_motion(self, tok: str, count: int, explicit: bool = False) -> None:
        target, _, _ = self.motion_target(tok, count, explicit)
        self._goto(target)
        if self.mode in ("visual", "visual_line"):
            self._update_visual()

    def _first_non_blank(self, row: int) -> int:
        line = self._line(row)
        return len(line) - len(line.lstrip()) if line.strip() else 0

    def _cls(self, ch: str, big: bool) -> int:
        cls = _char_class(ch)
        return 1 if big and cls else cls

    def _iter_forward(self, loc: Location):
        row, col = loc
        while row <= self._last_row:
            line = self._line(row)
            while col < len(line):
                yield (row, col), line[col]
                col += 1
            yield (row, len(line)), "\n"  # line break counts as whitespace
            row, col = row + 1, 0

    def _iter_backward(self, loc: Location):
        row, col = loc
        while row >= 0:
            line = self._line(row)
            col = min(col, len(line))
            while col > 0:
                col -= 1
                yield (row, col), line[col]
            if row == 0:
                return
            row -= 1
            col = len(self._line(row))
            yield (row, col), "\n"

    def _word_forward(self, loc: Location, big: bool = False) -> Location:
        it = self._iter_forward(loc)
        try:
            _, first = next(it)
        except StopIteration:
            return loc
        start_cls = self._cls(first, big)
        last = loc
        seen_space = start_cls == 0
        for pos, ch in it:
            last = pos
            cls = self._cls(ch, big)
            if cls == 0:
                seen_space = True
                if ch == "\n" and pos[1] == 0 and pos[0] != loc[0]:
                    return pos  # empty line is a word
                continue
            if seen_space or cls != start_cls:
                return pos
        return self._clamp(last)

    def _word_backward(self, loc: Location, big: bool = False) -> Location:
        chars = list(self._iter_backward(loc))
        i = 0
        while i < len(chars) and self._cls(chars[i][1], big) == 0:
            i += 1
        if i >= len(chars):
            return (0, 0)
        cls = self._cls(chars[i][1], big)
        while i + 1 < len(chars) and self._cls(chars[i + 1][1], big) == cls:
            i += 1
        return chars[i][0]

    def _word_end(self, loc: Location, big: bool = False) -> Location:
        it = self._iter_forward(loc)
        try:
            next(it)  # move off the current char
        except StopIteration:
            return loc
        prev: tuple[Location, str] | None = None
        cls = None
        for pos, ch in it:
            c = self._cls(ch, big)
            if cls is None:
                if c == 0:
                    continue
                cls = c
                prev = (pos, ch)
                continue
            if c != cls:
                return prev[0]
            prev = (pos, ch)
        return self._clamp(prev[0] if prev else loc)

    # ── operators ───────────────────────────────────────────

    def _operate_motion(self, op: str, tok: str, count: int, explicit: bool) -> None:
        if not self._is_motion(tok):
            return
        start = self.cursor_location
        # `cw` behaves like `ce` when on a word (vim quirk everyone relies on).
        if op == "c" and tok in ("w", "W") and self._line(start[0])[start[1]:start[1] + 1].strip():
            tok = "e" if tok == "w" else "E"
        target, linewise, inclusive = self.motion_target(tok, count, explicit)
        if linewise:
            self._operate_range_lines(op, min(start[0], target[0]), max(start[0], target[0]))
            return
        a, b = sorted([start, target])
        if inclusive:
            b = (b[0], b[1] + 1)
        if tok in ("w", "W") and op in ("d", "y") and b[0] > a[0]:
            # dw at the end of a line doesn't eat the line break
            b = (a[0], len(self._line(a[0])))
        self._operate_chars(op, a, self._clamp(b, allow_eol=True))

    def _operate_lines(self, op: str, count: int) -> None:
        row = self.cursor_location[0]
        self._operate_range_lines(op, row, min(self._last_row, row + count - 1))

    def _operate_range_lines(self, op: str, r1: int, r2: int) -> None:
        lines = self.lines
        chunk = lines[r1 : r2 + 1]
        if op in ("d", "c", "y"):
            self.register = _Register("\n".join(chunk) + "\n", linewise=True)
        if op == "y":
            self._notice(f"{len(chunk)} line{'s' if len(chunk) != 1 else ''} yanked" if len(chunk) > 1 else "")
            self._goto((r1, self.cursor_location[1]))
            return
        self._checkpoint()
        if op == "d":
            if r2 < self._last_row:
                self.delete((r1, 0), (r2 + 1, 0))
            elif r1 > 0:
                self.delete((r1 - 1, len(lines[r1 - 1])), (r2, len(lines[r2])))
                r1 -= 1
            else:
                self.delete((0, 0), (r2, len(lines[r2])))
            self._goto((min(r1, self._last_row), self._first_non_blank(min(r1, self._last_row))))
            return
        if op == "c":
            indent = chunk[0][: len(chunk[0]) - len(chunk[0].lstrip())]
            self.replace(indent, (r1, 0), (r2, len(lines[r2])))
            self.set_mode("insert")
            self._goto((r1, len(indent)), allow_eol=True)
            return
        if op in (">", "<"):
            width = self.indent_width or 2
            for r in range(r1, r2 + 1):
                line = self._line(r)
                if op == ">":
                    if line.strip():
                        self.insert(" " * width, (r, 0))
                else:
                    strip = min(width, len(line) - len(line.lstrip(" ")))
                    if strip:
                        self.delete((r, 0), (r, strip))
            self._goto((r1, self._first_non_blank(r1)))

    def _operate_chars(self, op: str, a: Location, b: Location) -> None:
        if a == b:
            if op == "c":
                self.set_mode("insert")
            return
        text = self.get_text_range(a, b)
        self.register = _Register(text, linewise=False)
        if op == "y":
            self._goto(a)
            return
        self._checkpoint()
        self.delete(a, b)
        if op == "c":
            self.set_mode("insert")
            self._goto(a, allow_eol=True)
        else:
            self._goto(a)

    # ── edits ───────────────────────────────────────────────

    def _open_line(self, below: bool) -> None:
        row = self.cursor_location[0]
        line = self._line(row)
        indent = line[: len(line) - len(line.lstrip())]
        # YAML: opening below a "key:" line indents one level deeper.
        if below and line.rstrip().endswith(":"):
            indent += " " * (self.indent_width or 2)
        if below:
            self.insert("\n" + indent, (row, len(line)))
            target = (row + 1, len(indent))
        else:
            self.insert(indent + "\n", (row, 0))
            target = (row, len(indent))
        self.set_mode("insert")
        self._goto(target, allow_eol=True)

    def _replace_char(self, ch: str) -> None:
        row, col = self.cursor_location
        if col < len(self._line(row)):
            self._checkpoint()
            self.replace(ch, (row, col), (row, col + 1))
            self._goto((row, col))

    def _toggle_case(self, count: int) -> None:
        row, col = self.cursor_location
        line = self._line(row)
        end = min(len(line), col + count)
        if col < end:
            self._checkpoint()
            self.replace(line[col:end].swapcase(), (row, col), (row, end))
            self._goto((row, end))

    def _put(self, after: bool, count: int) -> None:
        reg = self.register
        if not reg.text:
            return
        self._checkpoint()
        row, col = self.cursor_location
        if reg.linewise:
            block = reg.text * count
            if after:
                end = len(self._line(row))
                self.insert("\n" + block.rstrip("\n"), (row, end))
                target_row = row + 1
            else:
                self.insert(block, (row, 0))
                target_row = row
            self._goto((target_row, self._first_non_blank(target_row)))
        else:
            text = reg.text * count
            at = (row, min(col + 1, len(self._line(row)))) if after and self._line(row) else (row, col)
            result = self.insert(text, at)
            end = result.end_location
            self._goto((end[0], max(0, end[1] - 1)))

    def _join(self, times: int) -> None:
        self._checkpoint()
        for _ in range(times):
            row = self.cursor_location[0]
            if row >= self._last_row:
                break
            line, nxt = self._line(row), self._line(row + 1)
            joiner = "" if not nxt.strip() or line.endswith(" ") else " "
            self.replace(joiner + nxt.lstrip(), (row, len(line)), (row + 1, len(nxt)))
            self._goto((row, len(line)))

    # ── visual mode ─────────────────────────────────────────

    def _visual_range(self) -> tuple[Location, Location]:
        anchor = self._visual_anchor or self.cursor_location
        cur = self.cursor_location
        a, b = sorted([anchor, cur])
        if self.mode == "visual_line":
            return (a[0], 0), (b[0], len(self._line(b[0])))
        return a, (b[0], min(len(self._line(b[0])), b[1] + 1))

    def _update_visual(self) -> None:
        a, b = self._visual_range()
        anchor = self._visual_anchor or a
        if self.cursor_location >= anchor:
            self.selection = Selection(a, b)
        else:
            self.selection = Selection(b, a)

    def _visual(self, tok: str, count: int, explicit: bool) -> None:
        if self._is_motion(tok):
            self._apply_motion(tok, count, explicit)
            return
        if tok == "g":
            self._pending, self._pending_count = "g", count
            return
        if tok in ("v", "V"):
            target = "visual" if tok == "v" else "visual_line"
            if self.mode == target:
                self.set_mode("normal")
            else:
                self.mode = target
                self._update_visual()
            return
        if tok == "o" and self._visual_anchor is not None:
            cur = self.cursor_location
            self._goto(self._visual_anchor)
            self._visual_anchor = cur
            self._update_visual()
            return
        if tok in ("d", "x", "y", "c", "s", ">", "<", "D", "X", "Y", "C", "S", "~"):
            a, b = self._visual_range()
            linewise = self.mode == "visual_line" or tok in ("D", "X", "Y", "C", "S")
            op = {"x": "d", "s": "c", "D": "d", "X": "d", "Y": "y", "C": "c", "S": "c"}.get(tok, tok)
            self.set_mode("normal")
            self._goto(a)
            if op == "~":
                self._checkpoint()
                self.replace(self.get_text_range(a, b).swapcase(), a, b)
                self._goto(a)
            elif linewise or op in (">", "<"):
                self._operate_range_lines(op, a[0], b[0])
            else:
                self._operate_chars(op, a, b)

    # ── search ──────────────────────────────────────────────

    def search(self, pattern: str, forward: bool = True) -> bool:
        if not pattern:
            pattern = self.last_search or ""
        if not pattern:
            return False
        self.last_search = pattern
        self._search_forward = forward
        return self.search_next()

    def search_next(self, reverse: bool = False) -> bool:
        if not self.last_search:
            self._notice("No previous search", error=True)
            return False
        forward = self._search_forward != reverse
        try:
            rx = re.compile(self.last_search)
        except re.error:
            rx = re.compile(re.escape(self.last_search))
        lines = self.lines
        row, col = self.cursor_location
        n = len(lines)
        order = [(row + i) % n for i in range(n + 1)] if forward else [(row - i) % n for i in range(n + 1)]
        for i, r in enumerate(order):
            matches = [m.start() for m in rx.finditer(lines[r])]
            if forward:
                cands = [c for c in matches if (i > 0 or c > col)]
                hit = cands[0] if cands else None
            else:
                cands = [c for c in matches if (i > 0 or c < col)]
                hit = cands[-1] if cands else None
            if hit is not None:
                if (forward and i == n) or (not forward and i == n):
                    pass
                wrapped = (forward and r < row) or (not forward and r > row)
                self._goto((r, hit))
                self._notice("search hit BOTTOM, continuing at TOP" if wrapped and forward else
                             "search hit TOP, continuing at BOTTOM" if wrapped else "")
                return True
        self._notice(f"Pattern not found: {self.last_search}", error=True)
        return False
