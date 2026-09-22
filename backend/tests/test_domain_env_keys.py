"""Reserved .env keys (R2-02): names that change how the API process or the
graphrag subprocess resolves binaries, Python, or transport (proxies, CA
bundles) are never accepted from a project's .env."""

import pytest

from graphrag_ui.domain.env_keys import is_reserved_env_key


@pytest.mark.parametrize(
    "key",
    [
        "PATH",
        "HOME",
        "TMPDIR",
        "PYTHONPATH",
        "PYTHONSTARTUP",
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "DYLD_INSERT_LIBRARIES",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
    ],
)
def test_process_and_transport_keys_are_reserved(key):
    assert is_reserved_env_key(key)


@pytest.mark.parametrize(
    "key",
    [
        "GRAPHRAG_API_KEY",
        "OPENAI_API_KEY",
        "AZURE_OPENAI_ENDPOINT",
        "OPENAI_BASE_URL",
        "TITLE_COLUMN",
    ],
)
def test_llm_and_project_keys_are_allowed(key):
    assert not is_reserved_env_key(key)
