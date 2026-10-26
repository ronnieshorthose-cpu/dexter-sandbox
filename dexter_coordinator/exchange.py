"""Private folders for handoff text, pasted replies, reports, backups and the dashboard page.

They sit next to the database (by default ~/.dexter/), never inside a git
working tree, so nothing here can be committed by accident:

    ~/.dexter/coordinator.db
    ~/.dexter/exchange/outbox/HO-0001.txt     handoff messages to copy
    ~/.dexter/exchange/inbox/HO-0001-reply.txt replies you paste in (deleted once recorded)
    ~/.dexter/exchange/reports/...             saved progress reports
    ~/.dexter/exchange/dashboard.html          the read-only status page
    ~/.dexter/backups/...                      database backups

Folders are created readable only by your account (mode 700 on macOS and
Linux; on Windows your user folder is private by default). Symbolic links are
refused, and new files never replace existing ones, except the dashboard page,
which is rebuilt each time.

Opening files uses the operating system's own handler (os.startfile on
Windows, the webbrowser module for the dashboard page). The clipboard uses
tkinter, which ships with Python. Nothing here uses the network.
"""

import os
import sys
from pathlib import Path

from .handoff import MAX_RESPONSE_CHARS
from .models import DexterError, ValidationError, safe_display
from .store import inside_git_work_tree

OUTBOX = "outbox"
INBOX = "inbox"
REPORTS = "reports"
DASHBOARD_FILE = "dashboard.html"


class ExchangeError(DexterError):
    """A private folder or file could not be used safely."""


class Exchange:
    def __init__(self, db_path, *, allow_inside_git=False):
        self.base = os.path.dirname(os.path.abspath(db_path))
        self.root = os.path.join(self.base, "exchange")
        self.backups = os.path.join(self.base, "backups")
        self.allow_inside_git = allow_inside_git

    # -- folders -------------------------------------------------------------------
    def folder(self, *parts):
        """Return a private folder under the exchange root, creating it if needed."""
        self._private_dir(self.base)
        self._private_dir(self.root)
        path = os.path.join(self.root, *parts)
        if parts:
            self._private_dir(path)
        return path

    def backups_folder(self):
        self._private_dir(self.base)
        self._private_dir(self.backups)
        return self.backups

    def _private_dir(self, path):
        if not self.allow_inside_git and inside_git_work_tree(os.path.join(path, "x")):
            raise ExchangeError(
                f"refusing to keep Dexter's files inside a git working tree ({path}), where they could be "
                "committed by accident. Use a database outside the repository, such as ~/.dexter/coordinator.db"
            )
        if os.path.islink(path):
            raise ExchangeError(f"{path} is a symbolic link; Dexter refuses to use it")
        if not os.path.isdir(path):
            if os.path.lexists(path):
                raise ExchangeError(f"{path} exists but is not a folder")
            try:
                os.mkdir(path, 0o700)
            except OSError as exc:
                raise ExchangeError(f"cannot create the folder {path}: {exc.strerror}") from None

    # -- files ---------------------------------------------------------------------
    def save_handoff(self, handoff):
        """Write a handoff message to the outbox and return its path. Reuses an identical file."""
        folder = self.folder(OUTBOX)
        text = safe_display(handoff["message"])
        for n in range(1, 100):
            name = f"{handoff['id']}.txt" if n == 1 else f"{handoff['id']}-{n}.txt"
            path = os.path.join(folder, name)
            if os.path.lexists(path):
                if not os.path.islink(path) and _read_text(path, limit=len(text) + 1) == text:
                    return path
                continue
            _write_new(path, text)
            return path
        raise ExchangeError(f"too many copies of {handoff['id']} in {folder}; delete some of them")

    def reply_file(self, handoff_id):
        """The file to paste a reply into. Created empty if missing; an existing one is kept."""
        path = os.path.join(self.folder(INBOX), f"{handoff_id}-reply.txt")
        if os.path.islink(path):
            raise ExchangeError(f"{path} is a symbolic link; Dexter refuses to use it")
        if not os.path.lexists(path):
            _write_new(path, "")
        return path

    def read_reply(self, path):
        if os.path.islink(path):
            raise ExchangeError(f"{path} is a symbolic link; Dexter refuses to read it")
        return _read_text(path, limit=MAX_RESPONSE_CHARS + 1)

    def discard_reply(self, path):
        """Delete a reply file once its text is safely recorded in the database."""
        try:
            if not os.path.islink(path):
                os.remove(path)
        except OSError:
            pass

    def save_report(self, project_id, stamp, text):
        path = os.path.join(self.folder(REPORTS), f"{project_id}-report-{stamp}.md")
        _write_new(path, text)
        return path

    def backup_path(self, stamp):
        return os.path.join(self.backups_folder(), f"coordinator-backup-{stamp}.db")

    def write_dashboard(self, html_text):
        """Rebuild the dashboard page. The new page is written privately, then swapped in."""
        target = os.path.join(self.root, DASHBOARD_FILE)
        self.folder()
        if os.path.islink(target):
            raise ExchangeError(f"{target} is a symbolic link; Dexter refuses to replace it")
        temporary = target + ".new"
        if os.path.lexists(temporary):
            if os.path.islink(temporary):
                raise ExchangeError(f"{temporary} is a symbolic link; Dexter refuses to use it")
            os.remove(temporary)
        _write_new(temporary, html_text)
        os.replace(temporary, target)
        return target


def _write_new(path, text):
    if os.path.lexists(path):  # also a dangling symbolic link, which Windows would follow
        raise ExchangeError(f"{path} already exists; Dexter never replaces existing files")
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError:
        raise ExchangeError(f"{path} already exists; Dexter never replaces existing files") from None
    except OSError as exc:
        raise ExchangeError(f"cannot create {path}: {exc.strerror}") from None
    with os.fdopen(fd, "wb") as f:
        f.write(text.replace("\r\n", "\n").replace("\n", os.linesep).encode("utf-8"))


def _read_text(path, *, limit):
    try:
        # utf-8-sig: Notepad may start the file with a byte-order mark.
        with open(path, "r", encoding="utf-8-sig", newline=None) as f:
            return f.read(limit)
    except FileNotFoundError:
        raise ValidationError(f"there is no file {path}") from None
    except UnicodeDecodeError:
        raise ValidationError(f"{path} is not UTF-8 text. In Notepad, use File > Save As and choose UTF-8") from None
    except OSError as exc:
        raise ValidationError(f"cannot read {path}: {exc.strerror}") from None


# -- opening files and the clipboard ---------------------------------------------------

def open_text_file(path):
    """Open a text file in the default editor (Notepad on Windows). Returns True if asked to open it.

    Only Windows has a reliable built-in way to do this without starting other
    programs ourselves; elsewhere Dexter prints the path instead.
    """
    if sys.platform == "win32":
        try:
            os.startfile(path)  # opens .txt files in Notepad unless the owner chose another editor
            return True
        except OSError:
            return False
    return False


def open_in_browser(path):
    """Open a local file in the web browser. The page is a file, not a server."""
    import webbrowser

    try:
        return bool(webbrowser.open(Path(path).resolve().as_uri()))
    except Exception:  # no browser available: the caller prints the path
        return False


def copy_to_clipboard(text):
    """Best effort. Returns (True, "") or (False, reason). Never raises.

    Uses tkinter's clipboard, which keeps the text after Dexter's hidden window
    closes on Windows and macOS. On Linux the text vanishes with the window, so
    Dexter does not offer it there.
    """
    if sys.platform.startswith("linux"):
        return False, "the clipboard is not supported on Linux; use the file or the on-screen copy instead"
    try:
        import tkinter
    except ImportError:
        return False, "Python's tkinter module is not installed (it is part of the python.org installer)"
    try:
        root = tkinter.Tk()
        try:
            root.withdraw()
            root.clipboard_clear()
            root.clipboard_append(safe_display(text))
            root.update()
        finally:
            root.destroy()
    except Exception as exc:  # tkinter.TclError and friends: no display, clipboard busy...
        return False, f"the clipboard could not be used ({type(exc).__name__})"
    return True, ""
