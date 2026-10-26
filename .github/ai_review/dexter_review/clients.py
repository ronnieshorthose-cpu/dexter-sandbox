"""Minimal GitHub and OpenAI HTTP clients (standard library only).

Both take an injectable `transport` so tests never touch the network.
Error messages never include request headers, tokens, or response bodies
beyond a short machine-readable error code.
"""

import http.client
import json
import re
import urllib.error
import urllib.request

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
OPENAI_URL = "https://api.openai.com/v1/responses"
GITHUB_API = "https://api.github.com"

# Every way a request can fail below the HTTP layer: DNS, TLS, timeouts, resets,
# and a connection dropped while the body is being read (IncompleteRead).
_NETWORK_ERRORS = (OSError, http.client.HTTPException)


class HTTPError(Exception):
    def __init__(self, status, message, code=""):
        super().__init__(message)
        self.status = status
        self.code = code


def urllib_transport(method, url, headers, body, timeout):
    """Real transport. Returns (status, body_bytes). Raises HTTPError(0, ...) on network errors."""
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, exc.read()
        except _NETWORK_ERRORS:
            return exc.code, b""
    except _NETWORK_ERRORS as exc:
        raise HTTPError(0, f"network error: {type(exc).__name__}") from None


def _error_code(body):
    """Extract a short error code/type from a JSON error body, without echoing content."""
    try:
        data = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return ""
    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(err, dict):
        for key in ("code", "type"):
            value = err.get(key)
            if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", value):
                return value
    return ""


class GitHubClient:
    def __init__(self, repo, token, transport=urllib_transport, timeout=30):
        self.repo = repo
        self._token = token
        self._transport = transport
        self._timeout = timeout

    def _request(self, method, path, payload=None, accept="application/vnd.github+json"):
        headers = {
            "Accept": accept,
            "Authorization": f"Bearer {self._token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "dexter-ai-review",
        }
        body = None
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        status, raw = self._transport(method, GITHUB_API + path, headers, body, self._timeout)
        if status >= 400:
            raise HTTPError(status, f"GitHub API {method} {path.split('?')[0]} returned HTTP {status}")
        return raw

    def _json(self, method, path, payload=None):
        raw = self._request(method, path, payload)
        try:
            return json.loads(raw.decode("utf-8")) if raw else None
        except (ValueError, UnicodeDecodeError):
            raise HTTPError(0, f"GitHub API {method} returned invalid JSON") from None

    def get_pull(self, number):
        return self._json("GET", f"/repos/{self.repo}/pulls/{int(number)}")

    def get_compare_diff(self, base_sha, head_sha):
        """Diff pinned to exact SHAs (three-dot: from the merge base to head)."""
        if not (SHA_RE.fullmatch(base_sha) and SHA_RE.fullmatch(head_sha)):
            raise ValueError("invalid commit SHA")
        raw = self._request(
            "GET",
            f"/repos/{self.repo}/compare/{base_sha}...{head_sha}",
            accept="application/vnd.github.diff",
        )
        return raw.decode("utf-8", errors="replace")

    def _list_all(self, path, what, max_pages):
        """Read every page, or raise: a truncated list could hide the spending counter."""
        items = []
        for page in range(1, max_pages + 1):
            batch = self._json("GET", f"{path}?per_page=100&page={page}")
            if not isinstance(batch, list):
                raise HTTPError(0, f"GitHub API returned an unexpected {what} list")
            items.extend(batch)
            if len(batch) < 100:
                return items
        raise HTTPError(0, f"too many {what}s to read them all safely")

    def list_issue_comments(self, number, max_pages=10):
        return self._list_all(f"/repos/{self.repo}/issues/{int(number)}/comments", "comment", max_pages)

    def list_statuses(self, sha, max_pages=10):
        if not SHA_RE.fullmatch(sha):
            raise ValueError("invalid commit SHA")
        return self._list_all(f"/repos/{self.repo}/commits/{sha}/statuses", "status", max_pages)

    def create_comment(self, number, body):
        return self._json("POST", f"/repos/{self.repo}/issues/{int(number)}/comments", {"body": body})

    def update_comment(self, comment_id, body):
        return self._json(
            "PATCH", f"/repos/{self.repo}/issues/comments/{int(comment_id)}", {"body": body}
        )

    def create_status(self, sha, state, description, context, target_url=""):
        if not SHA_RE.fullmatch(sha):
            raise ValueError("invalid commit SHA")
        if state not in ("pending", "success", "failure", "error"):
            raise ValueError("invalid status state")
        payload = {"state": state, "description": description[:140], "context": context}
        if target_url:
            payload["target_url"] = target_url
        return self._json("POST", f"/repos/{self.repo}/statuses/{sha}", payload)


class OpenAIError(Exception):
    def __init__(self, message, retryable, status=0, code=""):
        super().__init__(message)
        self.retryable = retryable
        self.status = status
        self.code = code


class OpenAIClient:
    """Calls the Responses API. Every attempt that may reach OpenAI is counted."""

    def __init__(self, api_key, transport=urllib_transport, sleep=None, timeout=120, max_attempts=2):
        self._api_key = api_key
        self._transport = transport
        self._sleep = sleep
        self._timeout = timeout
        self.max_attempts = max_attempts
        self.attempts = 0

    def create_response(self, payload):
        if not self._api_key:
            raise OpenAIError("no API key available", retryable=False)
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "User-Agent": "dexter-ai-review",
        }
        body = json.dumps(payload).encode("utf-8")
        last_error = None
        for attempt in range(1, self.max_attempts + 1):
            self.attempts += 1
            try:
                status, raw = self._transport("POST", OPENAI_URL, headers, body, self._timeout)
            except HTTPError as exc:
                last_error = OpenAIError(str(exc), retryable=True)
            else:
                if status < 400:
                    try:
                        return json.loads(raw.decode("utf-8"))
                    except (ValueError, UnicodeDecodeError):
                        raise OpenAIError("OpenAI returned a non-JSON response", retryable=False) from None
                code = _error_code(raw)
                retryable = (status == 429 and code != "insufficient_quota") or status >= 500
                last_error = OpenAIError(
                    f"OpenAI API returned HTTP {status}" + (f" ({code})" if code else ""),
                    retryable=retryable,
                    status=status,
                    code=code,
                )
            if not last_error.retryable or attempt == self.max_attempts:
                raise last_error
            if self._sleep:
                self._sleep(2 ** attempt)
        raise last_error
