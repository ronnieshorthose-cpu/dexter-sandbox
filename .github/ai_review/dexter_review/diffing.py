"""Split a unified diff into files, drop files that must not be sent, and cap size."""

import fnmatch
import re
from collections import Counter
from dataclasses import dataclass, field

# Generated files: never sent, to save cost. A pull request author can give any
# file such a name (for example `src/payload.min.js`), and lockfiles can point
# at malicious packages, so leaving one out still makes the review PARTIAL.
GENERATED_PATTERNS = (
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "Pipfile.lock",
    "Cargo.lock", "go.sum", "composer.lock", "*.min.js", "*.min.css", "*.map",
)

# Files whose names suggest secrets: never sent. Because a pull request could
# use such a name to keep code away from the reviewer, withholding one makes
# the review PARTIAL rather than complete.
SENSITIVE_PATTERNS = (
    ".env", ".env.*", "*.env", "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa*", "id_ed25519*",
    "*secret*", "*credential*", "*.sqlite", "*.db",
)

# Content that looks like a credential. Files containing a match are withheld.
SECRET_PATTERNS = tuple(
    re.compile(p)
    for p in (
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
        r"\bsk-[A-Za-z0-9_-]{20,}",
        r"\b[sr]k_live_[A-Za-z0-9]{16,}",
        r"\bAKIA[0-9A-Z]{16}\b",
        r"\bAIza[0-9A-Za-z_-]{35}",
        r"\bgh[pousr]_[A-Za-z0-9]{30,}",
        r"\bgithub_pat_[A-Za-z0-9_]{30,}",
        r"\bxox[abpre]-[A-Za-z0-9-]{10,}",
    )
)

_HEADER_PREFIX = "diff --git "
_ESCAPES = {"a": 7, "b": 8, "t": 9, "n": 10, "v": 11, "f": 12, "r": 13, '"': 34, "\\": 92}

UNPARSED_PATH = "(file name could not be read)"

REASON_POLICY = "generated file; not sent to the AI and not reviewed"
REASON_SENSITIVE = "sensitive file name; withheld from the AI and not reviewed"
REASON_UNPARSED = "diff header could not be parsed; withheld"
REASON_BINARY = "binary file"
REASON_SECRET = "possible secret detected; not sent to the AI"
REASON_SIZE = "diff size limit reached"
REASON_TRUNCATED = "truncated at diff size limit"
REASON_MISSING = "missing from the diff GitHub returned"
REASON_UNLISTED = "in the diff but not in GitHub's changed-file list"
REASON_DUPLICATE = "appears more than once in the diff"
REASON_UNCONFIRMED = "could not confirm that every changed file was included"


@dataclass
class FileDiff:
    path: str
    text: str


@dataclass
class DiffBundle:
    text: str
    reviewed_files: list = field(default_factory=list)
    omitted: list = field(default_factory=list)  # (path, reason)
    file_count: int = 0
    paths: list = field(default_factory=list)  # every parsed path, None where unreadable

    @property
    def is_partial(self):
        """True when any changed file was not sent to the reviewer in full, for any reason."""
        return bool(self.omitted)


def diff_lines(text):
    """Split on LF only, keeping the line ends.

    str.splitlines() also breaks on \\v, \\f, \\x1c-\\x1e, \\x85, U+2028 and
    U+2029. Git does not treat those as line ends, so a pull request could put
    one inside an added line and start a fake `diff --git` header with it.
    """
    parts = text.split("\n")
    lines = [part + "\n" for part in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines


def _unquote(text, start):
    """Decode a git C-style quoted path starting at text[start] == '"'.

    Returns (path, index after the closing quote), or (None, 0) if malformed.
    """
    out, i = bytearray(), start + 1
    while i < len(text):
        ch = text[i]
        if ch == '"':
            return out.decode("utf-8", errors="replace"), i + 1
        if ch != "\\":
            out += ch.encode("utf-8")
            i += 1
            continue
        nxt = text[i + 1:i + 2]
        if nxt in _ESCAPES:
            out.append(_ESCAPES[nxt])
            i += 2
        elif re.fullmatch(r"[0-3][0-7]{2}", text[i + 1:i + 4]):
            out.append(int(text[i + 1:i + 4], 8))
            i += 4
        else:
            return None, 0
    return None, 0


def parse_header(line):
    """Return the new-side path of a `diff --git` header, or None when it is ambiguous.

    Git quotes paths containing unusual characters (including non-ASCII), and
    paths may contain spaces, so a simple regex can attribute one file's
    changes to another.
    """
    rest = line[len(_HEADER_PREFIX):]
    if rest.startswith('"'):
        _, end = _unquote(rest, 0)
        if not end or rest[end:end + 1] != " ":
            return None
        tail = rest[end + 1:]
    elif rest.endswith('"') and ' "b/' in rest:
        tail = rest[rest.index(' "b/') + 1:]
    else:
        # Unquoted: "a/P b/P" (same path) is the common case and is unambiguous.
        half = (len(rest) - 5) // 2
        if rest.startswith("a/") and rest == f"a/{rest[2:2 + half]} b/{rest[2:2 + half]}":
            return rest[2:2 + half] or None
        if rest.startswith("a/") and rest.count(" b/") == 1:
            return rest.split(" b/", 1)[1] or None
        return None
    if tail.startswith('"'):
        path, end = _unquote(tail, 0)
        if path is None or end != len(tail):
            return None
    else:
        path = tail
    return path[2:] if path.startswith("b/") and len(path) > 2 else None


def split_diff(diff_text):
    files = []
    current_path, current_lines = None, None
    for line in diff_lines(diff_text):
        if line.startswith(_HEADER_PREFIX):
            if current_lines is not None:
                files.append(FileDiff(current_path, "".join(current_lines)))
            current_path, current_lines = parse_header(line.rstrip("\n").rstrip("\r")), [line]
        elif current_lines is not None:
            current_lines.append(line)
    if current_lines is not None:
        files.append(FileDiff(current_path, "".join(current_lines)))
    return files


def _matches(path, patterns):
    name = path.rsplit("/", 1)[-1].lower()
    return any(
        fnmatch.fnmatchcase(name, pattern) or fnmatch.fnmatchcase(path.lower(), pattern)
        for pattern in patterns
    )


def _is_binary(file_diff):
    return any(
        line.startswith("Binary files ") or line.startswith("GIT binary patch")
        for line in diff_lines(file_diff.text)[:20]
    )


def prepare_diff(diff_text, max_chars):
    bundle = DiffBundle(text="")
    parts, used = [], 0
    files = split_diff(diff_text)
    bundle.file_count = len(files)
    bundle.paths = [f.path for f in files]
    for file_diff in files:
        if file_diff.path is None:
            bundle.omitted.append((UNPARSED_PATH, REASON_UNPARSED))
            continue
        if _matches(file_diff.path, GENERATED_PATTERNS):
            bundle.omitted.append((file_diff.path, REASON_POLICY))
            continue
        if _matches(file_diff.path, SENSITIVE_PATTERNS):
            bundle.omitted.append((file_diff.path, REASON_SENSITIVE))
            continue
        if _is_binary(file_diff):
            bundle.omitted.append((file_diff.path, REASON_BINARY))
            continue
        if any(p.search(file_diff.text) for p in SECRET_PATTERNS):
            bundle.omitted.append((file_diff.path, REASON_SECRET))
            continue
        remaining = max_chars - used
        if remaining <= 0:
            bundle.omitted.append((file_diff.path, REASON_SIZE))
            continue
        text = file_diff.text
        if len(text) > remaining:
            text = text[:remaining] + "\n[... diff truncated by reviewer size limit ...]\n"
            bundle.omitted.append((file_diff.path, REASON_TRUNCATED))
        parts.append(text)
        used += len(text)
        bundle.reviewed_files.append(file_diff.path)
    bundle.text = "".join(parts)
    return bundle


def check_file_list(bundle, listed, expected):
    """Mark the bundle partial unless its files are exactly the PR's changed files.

    `listed` is GitHub's changed-file list for the PR (None if it could not be
    read) and `expected` the PR's `changed_files` count. Any file that is
    missing from the diff, extra, duplicated or unreadable makes the review
    PARTIAL, so a forged or mis-split file boundary cannot pass as complete.
    """
    names = None
    if isinstance(listed, list):
        names = [item.get("filename") if isinstance(item, dict) else None for item in listed]
        if not all(isinstance(name, str) and name for name in names):
            names = None
    count_ok = not isinstance(expected, bool) and isinstance(expected, int) and expected >= 0
    if names is None or not count_ok or len(names) != expected:
        # GitHub's list stops at 3,000 files; any doubt means the review is incomplete.
        bundle.omitted.append(("(all files)", REASON_UNCONFIRMED))
    if names is None:
        return
    parsed = Counter(path for path in bundle.paths if path is not None)
    wanted = Counter(names)
    for path in sorted(wanted - parsed):
        bundle.omitted.append((path, REASON_MISSING))
    for path in sorted(parsed - wanted):
        if path in wanted:
            bundle.omitted.append((path, REASON_DUPLICATE))
        else:
            bundle.omitted.append((path, REASON_UNLISTED))
