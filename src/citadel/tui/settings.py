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

from ..library import TOPICS_FILE, Library, clean_tag, folder_name, folder_tag, short_tag
from .fields import Field
from .screens import ConfirmScreen

CLOUD_WORDS = ("onedrive", "dropbox", "nextcloud", "google drive", "icloud")
BROWSERS = ("google-chrome", "chromium", "chromium-browser", "brave-browser", "microsoft-edge")


def shown(path: Path) -> str:
    try:
        inside = path.relative_to(Path.home()).as_posix()
        return "~" if inside == "." else "~/" + inside
    except ValueError:
        return str(path)


def locations(home: Path | None = None) -> list[Path]:
    """Where a library could go: each synced folder found in the home folder (OneDrive,
    Dropbox…, also under ~/Cloud), then the home folder itself (this computer only)."""
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
                    places.append(child)
    places.append(home)
    return list(dict.fromkeys(places))


def existing_libraries(places: list[Path]) -> list[Path]:
    """Citadel-<tag> folders already in those places (made on another computer)."""
    found = []
    for place in places:
        try:
            found += sorted(
                child
                for child in place.iterdir()
                if child.is_dir() and folder_tag(child) and (child / TOPICS_FILE).is_file()
            )
        except OSError:
            continue
    return found


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
    """Where a library goes and its tag: the folder is <location>/Citadel-<tag>. The
    first-run welcome, adding one, and (location fixed) renaming one's tag."""

    BINDINGS = [Binding("escape", "dismiss(None)", "Cancel")]

    def __init__(
        self,
        title: str,
        tag: str = "",
        location: str = "",
        hint: str = "",
        taken: tuple[str, ...] = (),
        rename: bool = False,
    ) -> None:
        super().__init__()
        self.title_text, self.hint, self.rename = title, hint, rename
        self.start_tag, self.start_location = tag, location
        self.taken = {short_tag(t) for t in taken}  # first letters in use
        self.places = [] if rename else locations()
        self.found = [] if rename else existing_libraries(self.places)

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog wide"):
            yield Static(self.title_text, classes="title")
            if self.hint:
                yield Static(self.hint, classes="subtitle")
            if not self.rename:
                yield Static("Location (Enter to pick one)", classes="field-label")
                options = [
                    Option(f"{shown(lib)}  [b](open this existing library)[/]", id=str(lib))
                    for lib in self.found
                ] + [
                    Option(
                        shown(place)
                        + ("  [dim](this computer only)[/]" if place == Path.home() else ""),
                        id=str(place),
                    )
                    for place in self.places
                ]
                yield OptionList(*options, id="places")
            yield Static("Location", classes="field-label")
            yield Field(
                self.start_location,
                placeholder="~/OneDrive",
                id="lib-location",
                disabled=self.rename,
            )
            yield Static(
                "Tag (personal, work, local…: its first letter marks the papers)",
                classes="field-label",
            )
            yield Field(self.start_tag, placeholder="personal", id="lib-tag")
            yield Static(id="lib-folder", classes="footnote")
            yield Static(
                "[b]Enter[/] in a field saves · [b]Esc[/] cancels · "
                + (
                    "the folder is renamed; nothing inside changes"
                    if self.rename
                    else "the folder is created if missing; nothing is moved or deleted"
                ),
                classes="footnote",
            )

    def on_mount(self) -> None:
        self.query_one("#lib-tag" if self.rename or self.start_location else "#places").focus()
        self.show_folder()

    def folder(self) -> Path | None:
        location = self.query_one("#lib-location", Input).value.strip()
        tag = clean_tag(self.query_one("#lib-tag", Input).value)
        return Path(location).expanduser() / folder_name(tag) if location and tag else None

    @on(Input.Changed)
    def show_folder(self) -> None:
        folder = self.folder()
        self.query_one("#lib-folder", Static).update(
            f"Folder: [b]{shown(folder)}[/]" if folder else "Folder: pick a location and a tag"
        )

    @on(OptionList.OptionSelected, "#places")
    def picked(self, event: OptionList.OptionSelected) -> None:
        place = Path(event.option.id)
        tag = folder_tag(place)
        if tag:  # an existing library: its location and tag
            self.query_one("#lib-location", Input).value = shown(place.parent)
            self.query_one("#lib-tag", Input).value = tag
        else:
            self.query_one("#lib-location", Input).value = shown(place)
        self.query_one("#lib-tag", Input).focus()

    @on(Input.Submitted)
    def submit(self) -> None:
        location = self.query_one("#lib-location", Input).value.strip()
        tag = clean_tag(self.query_one("#lib-tag", Input).value)
        if not location:
            self.notify("Pick or type a location.", severity="error")
            return
        if not tag:
            self.notify("Give the library a tag, such as personal or work.", severity="error")
            return
        if short_tag(tag) in self.taken:
            self.notify(
                f"Another library's tag starts with {short_tag(tag)!r}: each needs its own "
                "first letter.",
                severity="error",
            )
            return
        place = Path(location).expanduser()
        folder = place / folder_name(tag)
        if not place.is_dir():
            self.notify(
                f"{shown(place)} does not exist (is that drive mounted?).", severity="error"
            )
            return
        if folder.exists() and not folder.is_dir():
            self.notify(f"{shown(folder)} is a file, not a folder.", severity="error")
            return
        if self.rename and folder.exists():
            self.notify(f"{shown(folder)} already exists.", severity="error")
            return
        if Library(folder).unmounted():
            self.notify(
                f"{shown(place)} is empty and not a mounted drive. If it is your "
                "OneDrive, start it first; otherwise create the folder yourself.",
                severity="error",
            )
            return
        self.dismiss({"path": str(folder), "tag": tag})


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
                "[b]a[/] add a library · [b]e[/] rename its tag · [b]u[/] make default "
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
        return [{"path": str(lib.root)} for lib in self.app.libraries]

    def selected(self) -> int:
        return self.query_one("#lib-table", DataTable).cursor_row

    def save(self, entries: list[dict]) -> None:
        self.app.save_libraries(entries)
        self.refresh_view()

    def action_add(self) -> None:
        def added(result: dict | None) -> None:
            if result:
                self.save([*self.entries(), {"path": result["path"]}])

        self.app.push_screen(
            LibraryForm(
                "Add a library",
                hint="Another library, such as one on a work OneDrive. Each keeps its own "
                "papers in its own Citadel-<tag> folder; search covers them all.",
                taken=tuple(lib.name for lib in self.app.libraries),
            ),
            added,
        )

    def action_change(self) -> None:
        library = self.app.libraries[self.selected()]

        def renamed(result: dict | None) -> None:
            if result:
                self.app.rename_library(library, result["tag"], after=self.refresh_view)

        self.app.push_screen(
            LibraryForm(
                f"Rename the tag of {library.name}",
                tag=library.name,
                location=shown(library.root.parent),
                hint=f"Its folder {shown(library.root)} is renamed to match.",
                taken=tuple(lib.name for lib in self.app.libraries if lib is not library),
                rename=True,
            ),
            renamed,
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
