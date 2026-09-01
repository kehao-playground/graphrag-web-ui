"""Settings validation for the local-auth trust anchor (JWT_SECRET).

Proxy mode already refused a weak PROXY_AUTH_SECRET at startup; local mode
had no equivalent rule, so the shipped `.env.example` placeholder was a
working production secret. These tests pin the symmetric rule.
"""

import pytest

from graphrag_ui.config import Settings

STRONG = "x" * 32


def test_local_mode_rejects_the_shipped_placeholder():
    # The exact literal `.env.example` used to ship. `cp .env.example .env`
    # is the documented setup path, so this value reaching production is the
    # likely outcome, not the unlucky one — and anyone can then mint an
    # access token for any user id.
    with pytest.raises(ValueError, match="JWT_SECRET"):
        Settings(auth_mode="local", jwt_secret="dev-secret-change-me")


def test_local_mode_rejects_short_secret():
    with pytest.raises(ValueError, match="JWT_SECRET"):
        Settings(auth_mode="local", jwt_secret="x" * 31)


def test_local_mode_rejects_empty_secret():
    with pytest.raises(ValueError, match="JWT_SECRET"):
        Settings(auth_mode="local", jwt_secret="")


def test_local_mode_accepts_32_char_secret():
    assert Settings(auth_mode="local", jwt_secret=STRONG).jwt_secret == STRONG


def test_proxy_mode_ignores_jwt_secret():
    # Proxy mode registers no login/refresh routes and issues no tokens, so
    # JWT_SECRET is unused there — demanding one would be noise.
    s = Settings(auth_mode="proxy", proxy_auth_secret=STRONG, jwt_secret="")
    assert s.auth_mode == "proxy"


def test_placeholder_check_is_case_and_whitespace_insensitive():
    with pytest.raises(ValueError, match="JWT_SECRET"):
        Settings(auth_mode="local", jwt_secret="  Dev-Secret-Change-Me  ")


def test_a_long_secret_that_merely_contains_the_placeholder_is_allowed():
    # Substring matching would be a trap: rejecting anything containing the
    # placeholder would also reject a legitimately generated secret that
    # happens to embed it.
    value = "dev-secret-change-me" + "0123456789abcdef"
    assert Settings(auth_mode="local", jwt_secret=value).jwt_secret == value
