"""The workspace `.env` as the ONLY environment graphrag sees (R2-01, R2-02).

graphrag resolves `${VAR}` placeholders in `settings.yaml` from its process
environment and merges the workspace `.env` into it (`load_dotenv`,
override=False). Left alone, that lets any project owner read the API
process's secrets (`api_key: ${JWT_SECRET}`) and lets the first project
queried after boot define `GRAPHRAG_API_KEY` for every later one. This
module is the boundary instead: placeholders are substituted here from the
workspace `.env` alone, and the index/update/dry-run subprocesses get an
allowlisted environment, never `dict(os.environ)`.
"""

from __future__ import annotations

import os
import string
from collections.abc import Mapping
from pathlib import Path

from dotenv import dotenv_values

from graphrag_ui.domain.env_keys import is_reserved_env_key

# Process-level names the graphrag subprocess needs to run at all (binaries
# on PATH, home/tmp dirs, locale, the baked NLTK corpora) or that an OPERATOR
# legitimately sets for the deployment (egress proxy, private CA, tiktoken
# cache). Everything else in the API environment — JWT_SECRET, DATABASE_URL,
# bootstrap credentials, PROXY_AUTH_SECRET — stays out of the child.
_PASSTHROUGH_NAMES = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "TMPDIR",
        "TEMP",
        "TMP",
        "TZ",
        "LANG",
        "LANGUAGE",
        "LITELLM_LOG",
        "NLTK_DATA",
        "TIKTOKEN_CACHE_DIR",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
    }
)
# PYTHON* (unbuffered output keeps the job log streaming) and LC_* (locale).
_PASSTHROUGH_PREFIXES = ("PYTHON", "LC_")


def read_workspace_env(root: Path) -> dict[str, str]:
    """KEY=VALUE pairs of `<root>/.env`, parsed by python-dotenv — the same
    parser graphrag's loader uses, so quoting and inline comments resolve
    identically in-process and in the CLI. Reserved names are dropped (a
    .env written before the validator existed may still carry them);
    a missing file reads as empty."""
    path = root / ".env"
    if not path.is_file():
        return {}
    return {
        key: value
        for key, value in dotenv_values(path).items()
        if value is not None and not is_reserved_env_key(key)
    }


def substitute_placeholders(text: str, env: Mapping[str, str]) -> str:
    """Strict `string.Template` substitution, exactly as graphrag_common's
    loader applies it to settings.yaml before YAML parsing: an unknown
    `${NAME}` raises KeyError, a stray `$` raises ValueError, `$$` is a
    literal `$`."""
    return string.Template(text).substitute(env)


def subprocess_env(root: Path) -> dict[str, str]:
    """Environment for a graphrag CLI run in `root`: the passthrough
    allowlist from the API process, then the workspace `.env` pairs for any
    name the operator did not set. The child's own `load_dotenv` (override
    False) then finds nothing new to add, so `${VAR}` in its settings.yaml
    resolves from the workspace alone."""
    env = {
        name: value
        for name, value in os.environ.items()
        if name in _PASSTHROUGH_NAMES or name.startswith(_PASSTHROUGH_PREFIXES)
    }
    for key, value in read_workspace_env(root).items():
        env.setdefault(key, value)
    # litellm logs botocore pre-load warnings at import; its handler level
    # comes from LITELLM_LOG. setdefault: an operator's explicit DEBUG wins.
    env.setdefault("LITELLM_LOG", "ERROR")
    return env
