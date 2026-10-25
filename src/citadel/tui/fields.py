"""Text fields where Ctrl+A selects everything, as in every other editor.

Textual's own fields use Ctrl+A for "start of line" (Emacs style); Home still does that.
"""

from __future__ import annotations

from textual.binding import Binding
from textual.widgets import Input, TextArea


class Field(Input):
    BINDINGS = [Binding("ctrl+a", "select_all", "Select all", show=False)]


class Notes(TextArea):
    BINDINGS = [Binding("ctrl+a", "select_all", "Select all", show=False)]
