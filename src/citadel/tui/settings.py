"""Setting Citadel up from the app: the library folders (first run and Settings) and the
optional library sign-in for paywalled papers."""

from __future__ import annotations

import shutil
from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Input, OptionList, Select, Static
from textual.widgets.option_list import Option

from ..library import TOPICS_FILE, Library
from .fields import Field
from .screens import ConfirmScreen

CLOUD_WORDS = ("onedrive", "dropbox", "nextcloud", "google drive", "icloud")
BROWSERS = ("google-chrome", "chromium", "chromium-browser", "brave-browser", "microsoft-edge")


def shown(path: Path) -> str:
    try:
        return "~/" + path.relative_to(Path.home()).as_posix()
    except ValueError:
        return str(path)


def folder_suggestions(home: Path | None = None) -> list[Path]:
    """Where a library could go: a Citadel folder in each synced folder found in the home
    folder (OneDrive, Dropbox…, also under ~/Cloud), then one on this computer only."""
    home = home or Path.home()
    places: list[Path] = []
    for parent in (home, home / "Cloud"):
        try:
            children = sorted(parent.iterdir())
        except OSError:
            continue
        for child in children:
            name = child.name.casefold()
            if not child.name.startswith(".") and (
                parent.name == "Cloud" or any(word in name for word in CLOUD_WORDS)
            ):
                if child.is_dir():
                    places.append(child / "Citadel")
    places.append(home / "Citadel")
    return list(dict.fromkeys(places))


def browsers() -> list[tuple[str, str]]:
    """(label, command) of the Chromium-family browsers installed. A snap browser cannot
    keep its profile where Citadel keeps it, so it comes last, marked."""
    found = []
    for command in BROWSERS:
        path = shutil.which(command)
        if path:
            snap = "/snap/" in path
            found.append((f"{command}{'  (snap: sign-in will not work)' if snap else ''}", command))
    return sorted(found, key=lambda item: "snap" in item[0])


class LibraryForm(ModalScreen[dict | None]):
    """A library's name and folder: the first-run welcome, and adding or changing one."""

    BINDINGS = [Binding("escape", "dismiss(None)", "Cancel")]

    def __init__(
        self,
        title: str,
        name: str = "",
        path: str = "",
        hint: str = "",
        taken: tuple[str, ...] = (),
    ) -> None:
        super().__init__()
        self.title_text, self.hint = title, hint
        self.start_name, self.start_path = name, path
        self.taken = {t.casefold() for t in taken}
        self.places = folder_suggestions()

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog wide"):
            yield Static(self.title_text, classes="title")
            if self.hint:
                yield Static(self.hint, classes="subtitle")
            yield Static("Folders found (Enter to pick one)", classes="field-label")
            yield OptionList(
                *[
                    Option(
                        f"{shown(place)}"
                        + ("  [dim](this computer only)[/]" if place.parent == Path.home() else "")
                        + ("  [b](a library is already here)[/]" if self.is_library(place) else ""),
                        id=str(place),
                    )
                    for place in self.places
                ],
                id="places",
            )
            yield Static("Folder", classes="field-label")
            yield Field(self.start_path, placeholder="~/OneDrive/Citadel", id="lib-path")
            yield Static("Name (shown as its tag: Personal, Work, Local…)", classes="field-label")
            yield Field(self.start_name, placeholder="Personal", id="lib-name")
            yield Static(
                "[b]Enter[/] in a field saves · [b]Esc[/] cancels · the folder is created if "
                "missing; nothing is moved or deleted",
                classes="footnote",
            )

    @staticmethod
    def is_library(place: Path) -> bool:
        return (place / TOPICS_FILE).is_file()

    def on_mount(self) -> None:
        self.query_one("#places" if not self.start_path else "#lib-path").focus()

    @on(OptionList.OptionSelected, "#places")
    def picked(self, event: OptionList.OptionSelected) -> None:
        self.query_one("#lib-path", Input).value = shown(Path(event.option.id))
        self.query_one("#lib-name", Input).focus()

    @on(Input.Submitted)
    def submit(self) -> None:
        name = self.query_one("#lib-name", Input).value.strip()
        path = self.query_one("#lib-path", Input).value.strip()
        if not path:
            self.notify("Pick or type a folder.", severity="error")
            return
        if not name:
            self.notify("Give the library a name, such as Personal or Work.", severity="error")
            return
        if name.casefold() in self.taken:
            self.notify(f"There is already a library called {name}.", severity="error")
            return
        folder = Path(path).expanduser()
        if folder.exists() and not folder.is_dir():
            self.notify(f"{path} is a file, not a folder.", severity="error")
            return
        if not folder.parent.is_dir():
            self.notify(
                f"{shown(folder.parent)} does not exist (is that drive mounted?).",
                severity="error",
            )
            return
        if Library(folder).unmounted():
            self.notify(
                f"{shown(folder.parent)} is empty and not a mounted drive. If it is your "
                "OneDrive, start it first; otherwise create the folder yourself.",
                severity="error",
            )
            return
        self.dismiss({"name": name, "path": str(folder)})


class AccessForm(ModalScreen[dict | None]):
    """The optional library sign-in: an institution's sign-in page (and EZproxy prefix)."""

    BINDINGS = [Binding("escape", "dismiss(None)", "Cancel"), Binding("ctrl+s", "save", "Save")]

    def __init__(self, name: str, login_url: str, proxy: str, browser: str) -> None:
        super().__init__()
        self.values = {"name": name, "login_url": login_url, "proxy": proxy}
        self.browser = browser

    def compose(self) -> ComposeResult:
        found = browsers()
        commands = [command for _, command in found]
        with Vertical(classes="dialog wide"):
            yield Static("Paywall sign-in (optional)", classes="title")
            yield Static(
                "For papers your university or library pays for. Citadel opens its pages "
                "in a browser window of its own; you sign in there yourself, and PDFs you "
                "download there go to the inbox. Leave the name empty to turn it off.",
                classes="subtitle",
            )
            yield Static("Name (your institution, e.g. imperial)", classes="field-label")
            yield Field(self.values["name"], id="access-name")
            yield Static("Sign-in page", classes="field-label")
            yield Field(
                self.values["login_url"],
                placeholder="https://library-search.imperial.ac.uk/",
                id="access-login_url",
            )
            yield Static("EZproxy prefix (only if your library uses one)", classes="field-label")
            yield Field(
                self.values["proxy"],
                placeholder="https://proxy.example.org/login?url=",
                id="access-proxy",
            )
            yield Static("Browser", classes="field-label")
            if found:
                value = self.browser if self.browser in commands else commands[0]
                yield Select(found, value=value, allow_blank=False, id="access-browser")
            else:
                yield Static(
                    "[$warning]No Chrome or Chromium found: install Google Chrome to sign in.[/]"
                )
            yield Static(
                "[b]Ctrl+S[/] or [b]Enter[/] saves · [b]Esc[/] cancels", classes="footnote"
            )

    def on_mount(self) -> None:
        self.query_one("#access-name").focus()

    @on(Input.Submitted)
    def action_save(self) -> None:
        values = {
            key: self.query_one(f"#access-{key}", Input).value.strip()
            for key in ("name", "login_url", "proxy")
        }
        if values["name"] and not values["login_url"].startswith(("https://", "http://")):
            self.notify("The sign-in page must be a web address (https://…).", severity="error")
            return
        selects = self.query("#access-browser")
        values["browser"] = str(selects.first(Select).value) if selects else ""
        self.dismiss(values)


class SettingsScreen(ModalScreen[None]):
    """Libraries and sign-in, saved to the config by the app (see Citadel.save_libraries)."""

    BINDINGS = [
        Binding("escape", "dismiss(None)", "Close"),
        Binding("a", "add", "Add library"),
        Binding("e", "change", "Rename / move"),
        Binding("x", "remove", "Remove"),
        Binding("u", "default", "Make default"),
        Binding("s", "access", "Sign-in"),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog wide"):
            yield Static("Settings", classes="title")
            yield Static("Libraries", classes="section")
            yield DataTable(id="lib-table", cursor_type="row")
            yield Static(
                "[b]a[/] add a folder · [b]e[/] rename or change folder · [b]u[/] make default "
                "(new papers go there) · [b]x[/] remove from Citadel (the files stay)",
                classes="footnote",
            )
            yield Static("Paywall sign-in", classes="section")
            yield Static(id="access-status")
            yield Static(
                "[b]s[/] set up or turn off · then 🔑 in the top bar (or [b]L[/]) signs in",
                classes="footnote",
            )
            yield Static("[b]Esc[/] closes", classes="footnote")

    def on_mount(self) -> None:
        table = self.query_one("#lib-table", DataTable)
        table.add_columns("Tag", "Name", "Folder", "")
        self.refresh_view()
        table.focus()

    def refresh_view(self) -> None:
        table = self.query_one("#lib-table", DataTable)
        row = table.cursor_row
        table.clear()
        for number, library in enumerate(self.app.libraries):
            state = "default" if number == 0 else ""
            if not library.root.is_dir():
                state = "[$warning]not found[/]"
            table.add_row(
                self.app.library_tag(library.name), library.name, shown(library.root), state
            )
        if table.row_count:
            table.move_cursor(row=min(row, table.row_count - 1))
        access = self.app.config.access
        self.query_one("#access-status", Static).update(
            f"On: [b]{access.name}[/] · {access.login_url}"
            if access.name
            else "[dim]Off: pages open in your usual browser.[/]"
        )

    def entries(self) -> list[dict]:
        return [{"name": lib.name, "path": str(lib.root)} for lib in self.app.libraries]

    def selected(self) -> int:
        return self.query_one("#lib-table", DataTable).cursor_row

    def save(self, entries: list[dict]) -> None:
        self.app.save_libraries(entries)
        self.refresh_view()

    def action_add(self) -> None:
        def added(result: dict | None) -> None:
            if result:
                self.save([*self.entries(), result])

        self.app.push_screen(
            LibraryForm(
                "Add a library",
                hint="Another Citadel folder, such as one on a work OneDrive. Each library keeps "
                "its own papers; search covers them all.",
                taken=tuple(lib.name for lib in self.app.libraries),
            ),
            added,
        )

    def action_change(self) -> None:
        entries, number = self.entries(), self.selected()
        current = entries[number]

        def changed(result: dict | None) -> None:
            if result:
                entries[number] = result
                self.save(entries)

        self.app.push_screen(
            LibraryForm(
                f"Library {current['name']}",
                name=current["name"],
                path=shown(Path(current["path"])),
                hint="A new folder is used as it is: move the files there yourself first.",
                taken=tuple(e["name"] for i, e in enumerate(entries) if i != number),
            ),
            changed,
        )

    def action_default(self) -> None:
        entries, number = self.entries(), self.selected()
        entries.insert(0, entries.pop(number))
        self.save(entries)
        self.query_one("#lib-table", DataTable).move_cursor(row=0)

    def action_remove(self) -> None:
        entries, number = self.entries(), self.selected()
        if len(entries) == 1:
            self.notify("Citadel needs at least one library.", severity="warning")
            return
        name = entries[number]["name"]

        def confirmed(yes: bool | None) -> None:
            if yes:
                del entries[number]
                self.save(entries)

        self.app.push_screen(
            ConfirmScreen(f"Remove {name} from Citadel? Its folder and files are not touched."),
            confirmed,
        )

    def action_access(self) -> None:
        self.app.action_setup_access(after=self.refresh_view)
