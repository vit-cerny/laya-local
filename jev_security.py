import re
from urllib.parse import urlsplit, urlunsplit

from jev_config import ALLOWED_SCHEMES

SECRET_PATTERNS = (
    re.compile(r"apikey_[A-Za-z0-9_]{20,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"gh[opsu]_[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
)


def redact(text):
    """Scrub credential-shaped strings before they reach a log, a tool result or the
    user's LLM. Providers can echo an Authorization header back in an error message."""
    out = str(text)
    for pattern in SECRET_PATTERNS:
        out = pattern.sub(lambda match: match.group(0)[:4] + "***" + match.group(0)[-2:], out)
    return out


def safe_url(raw):
    """Validate an LLM-supplied start URL. Only http/https may be navigated: file:// would
    read local files straight back to the model, javascript:/data: execute, and chrome://
    exposes browser internals. The scheme was previously passed through unchecked."""
    candidate = (raw or "").strip()
    if not candidate:
        return None, None
    if not candidate.lower().startswith(ALLOWED_SCHEMES):
        return None, f"refused non-http(s) url: {redact(candidate[:60])}"
    return candidate, None


def hide_url_secrets(raw):
    """Drop userinfo and query/fragment values before a URL is persisted or served, since
    query strings routinely carry session tokens and userinfo carries credentials."""
    text = str(raw or "")
    try:
        parts = urlsplit(text)
    except ValueError:
        return redact(text)
    if not parts.scheme:
        return redact(text)
    netloc = parts.netloc.rsplit("@", 1)[-1]
    return urlunsplit((parts.scheme, netloc, parts.path, "<redacted>" if parts.query else "", ""))