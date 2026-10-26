"""Split a unified diff into files, drop files that must not be sent, and cap size."""

import fnmatch
import re
from dataclasses import dataclass, field

# Never sent to the model: generated, binary-ish, or likely to contain secrets.
EXCLUDED_PATTERNS = (
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "Pipfile.lock",
    "Cargo.lock", "go.sum", "composer.lock", "*.min.js", "*.min.css", "*.map",
    ".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa*", "id_ed25519*",
    "*secret*", "*credential*", "*.sqlite", "*.db",
)

# Content that looks like a credential. Files containing a match are withheld.
SECRET_PATTERNS = tuple(
    re.compile(p)
    for p in (
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
        r"\bsk-[A-Za-z0-9_-]{20,}",
        r"\bAKIA[0-9A-Z]{16}\b",
        r"\bgh[pousr]_[A-Za-z0-9]{30,}",
        r"\bgithub_pat_[A-Za-z0-9_]{30,}",
        r"\bxox[abpr]-[A-Za-z0-9-]{10,}",
    )
)

_HEADER_RE = re.compile(r"^diff --git a/(.+?) b/(.+)$")

REASON_POLICY = "excluded by policy"
REASON_BINARY = "binary file"
REASON_SECRET = "possible secret detected; not sent to the AI"
REASON_SIZE = "diff size limit reached"
REASON_TRUNCATED = "truncated at diff size limit"


@dataclass
class FileDiff:
    path: str
    text: str


@dataclass
class DiffBundle:
    text: str
    reviewed_files: list = field(default_factory=list)
    omitted: list = field(default_factory=list)  # (path, reason)

    @property
    def is_partial(self):
        """True when something the reviewer should have seen was not sent in full."""
        return any(reason != REASON_POLICY for _, reason in self.omitted)


def split_diff(diff_text):
    files = []
    current_path, current_lines = None, []
    for line in diff_text.splitlines(keepends=True):
        match = _HEADER_RE.match(line.rstrip("\n"))
        if match:
            if current_path is not None:
                files.append(FileDiff(current_path, "".join(current_lines)))
            current_path, current_lines = match.group(2), [line]
        elif current_path is not None:
            current_lines.append(line)
    if current_path is not None:
        files.append(FileDiff(current_path, "".join(current_lines)))
    return files


def _excluded(path):
    name = path.rsplit("/", 1)[-1].lower()
    return any(
        fnmatch.fnmatchcase(name, pattern) or fnmatch.fnmatchcase(path.lower(), pattern)
        for pattern in EXCLUDED_PATTERNS
    )


def _is_binary(file_diff):
    return any(
        line.startswith("Binary files ") or line.startswith("GIT binary patch")
        for line in file_diff.text.splitlines()[:20]
    )


def prepare_diff(diff_text, max_chars):
    bundle = DiffBundle(text="")
    parts, used = [], 0
    for file_diff in split_diff(diff_text):
        if _excluded(file_diff.path):
            bundle.omitted.append((file_diff.path, REASON_POLICY))
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
