"""Reserved workspace `.env` key names (R2-02).

A project's `.env` is the placeholder source for its `settings.yaml` and is
handed verbatim to the graphrag subprocess, so a project owner writes into
that process's environment. Names that change how a process finds binaries
(PATH), Python itself (PYTHON*), loaders (LD_*/DYLD_*), or routes and trusts
network traffic (proxies, CA bundles) must never come from there: any one of
them redirects or intercepts every LLM call the job makes. LLM-provider
names (`GRAPHRAG_API_KEY`, `OPENAI_*`, `AZURE_*`) stay allowed — choosing an
endpoint for one's own project is the point of the file.
"""

_RESERVED_NAMES = frozenset(
    {
        "PATH",
        "HOME",
        "TMPDIR",
        "TEMP",
        "TMP",
        "NO_PROXY",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
    }
)
_RESERVED_PREFIXES = ("PYTHON", "LD_", "DYLD_", "SSL_")
_RESERVED_SUFFIXES = ("_PROXY",)


def is_reserved_env_key(key: str) -> bool:
    """True when `key` may not be set through a project's .env."""
    return (
        key in _RESERVED_NAMES
        or key.startswith(_RESERVED_PREFIXES)
        or key.endswith(_RESERVED_SUFFIXES)
    )
