"""Write-side masking for anything an observation surface records.

The rule this module exists to enforce: **a credential is masked where it is
written, not where it is read**. A reader-side filter only protects the readers
that remember to apply it, and the observation surfaces here (stream event
metadata, the on-disk trace sidecar, the diagnostic log) each have several
readers — the trace panel, ``grep``, a support engineer with a copy of the data
volume. Masking at the single write point is the only version of this that is
mechanically true.

What gets masked:

* keyed forms — ``token=…``, ``api_key: …``, ``Authorization: Bearer …`` and
  their spelling variants — because MCP connection errors and provider errors
  routinely echo the request that failed;
* recognised credential prefixes (``sk-``, ``ghp_``, ``xoxb-``, ``AKIA…``)
  standing on their own, because those appear in prose error messages with no
  key in front of them.

A masked value keeps its last four characters *only* when the secret was long
enough that four characters cannot reconstruct it (16+). Shorter secrets are
replaced whole: ``sk-live-XXXX`` must leave nothing behind, and a tail would.
"""

from __future__ import annotations

import re
from typing import Any

#: Below this length a four-character tail is a meaningful fraction of the
#: secret, so nothing is kept.
_TAIL_MIN_LEN = 16
_TAIL_CHARS = 4
_MASK = "****"

# key = value / key: value, quoted or not.
_KEYED_RE = re.compile(
    r"(?i)\b("
    r"api[-_]?key|apikey|access[-_]?token|refresh[-_]?token|auth[-_]?token"
    r"|bearer[-_]?token|id[-_]?token|session[-_]?token|secret[-_]?key|client[-_]?secret"
    r"|secret|password|passwd|pwd|token|authorization|credential|credentials"
    r")"
    # The optional quote after the key is what makes the JSON spelling
    # ``"secret": "…"`` match the same rule as ``secret=…``.
    r"([\"']?\s*[:=]\s*)"
    r"([\"'])?"
    # ``Authorization: Bearer <token>`` must not have "Bearer" taken as the
    # value — the scheme rule below owns that shape and masks the real token.
    r"(?!(?:bearer|basic)\b)"
    r"([A-Za-z0-9._~+/=\-]{4,})"
    r"([\"'])?"
)

# Authorization: Bearer <token> / Basic <token>
_SCHEME_RE = re.compile(r"(?i)\b(bearer|basic)(\s+)([A-Za-z0-9._~+/=\-]{6,})")

# Standalone credentials recognisable by their own prefix. The optional
# ``[a-z]{2,10}-`` after ``sk-``/``pk-`` absorbs an environment label
# (``sk-live-…``, ``sk-proj-…``) and is deliberately bounded to lowercase
# letters: an unbounded prefix class would backtrack into the secret itself and
# leave most of it unmasked.
_PREFIXED_RE = re.compile(
    r"\b("
    r"sk-(?:[a-z]{2,10}-)?|pk-(?:[a-z]{2,10}-)?|rk-"  # OpenAI-family
    r"|gh[pousr]_"  # GitHub
    r"|xox[baprs]-"  # Slack
    r"|glpat-"  # GitLab
    r"|AKIA"  # AWS access key id
    r")[A-Za-z0-9_\-]{4,}"
)


def _mask(secret: str) -> str:
    if len(secret) >= _TAIL_MIN_LEN:
        return f"{_MASK}{secret[-_TAIL_CHARS:]}"
    return _MASK


def redact_text(value: str, *, max_chars: int = 0) -> str:
    """Return *value* with recognised credentials masked.

    ``max_chars`` truncates the (already masked) result; ``0`` means no limit.
    Truncation happens after masking so a secret cannot survive by sitting past
    the cut and then being reassembled from a longer copy elsewhere.
    """
    if not value:
        return ""
    try:
        # Scheme first: ``Authorization: Bearer <token>`` matches both rules,
        # and only this order masks the token rather than the word "Bearer".
        out = _SCHEME_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}{_mask(m.group(3))}", value)
        out = _KEYED_RE.sub(
            lambda m: (
                f"{m.group(1)}{m.group(2)}{m.group(3) or ''}{_mask(m.group(4))}{m.group(5) or ''}"
            ),
            out,
        )
        out = _PREFIXED_RE.sub(lambda m: f"{m.group(1)}{_MASK}", out)
    except Exception:  # pragma: no cover - masking must never raise
        out = value
    if max_chars and len(out) > max_chars:
        out = out[:max_chars] + "…"
    return out


def redact_obj(value: Any, *, max_chars: int = 0) -> Any:
    """Recursively apply :func:`redact_text` to every string inside *value*.

    Dict keys are left alone (they are field names, not values) and non-string
    scalars pass through untouched.
    """
    if isinstance(value, str):
        return redact_text(value, max_chars=max_chars)
    if isinstance(value, dict):
        return {key: redact_obj(item, max_chars=max_chars) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        converted = [redact_obj(item, max_chars=max_chars) for item in value]
        return type(value)(converted) if isinstance(value, tuple) else converted
    return value


__all__ = ["redact_obj", "redact_text"]
