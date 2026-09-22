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


# --- BOOTSTRAP_ADMIN_PASSWORD (R3-14) ---
# The same argument as JWT_SECRET: `.env.example` ships a placeholder and
# compose only checks presence, so a deployment that replaced JWT_SECRET
# alone would run with a publicly known admin credential until someone —
# anyone — logged in first and owned it.


def test_local_mode_rejects_the_shipped_bootstrap_placeholder():
    with pytest.raises(ValueError, match="BOOTSTRAP_ADMIN_PASSWORD"):
        Settings(
            auth_mode="local",
            jwt_secret=STRONG,
            bootstrap_admin_password="bootstrap-admin-change-me",
        )


def test_local_mode_rejects_short_bootstrap_password():
    with pytest.raises(ValueError, match="BOOTSTRAP_ADMIN_PASSWORD"):
        Settings(auth_mode="local", jwt_secret=STRONG, bootstrap_admin_password="x" * 11)


def test_bootstrap_placeholder_check_is_case_and_whitespace_insensitive():
    with pytest.raises(ValueError, match="BOOTSTRAP_ADMIN_PASSWORD"):
        Settings(
            auth_mode="local",
            jwt_secret=STRONG,
            bootstrap_admin_password="  Bootstrap-Admin-Change-Me ",
        )


def test_empty_bootstrap_password_still_means_do_not_create():
    # Empty is the documented "no bootstrap admin" switch, not a weak password.
    assert (
        Settings(
            auth_mode="local", jwt_secret=STRONG, bootstrap_admin_password=""
        ).bootstrap_admin_password
        == ""
    )


def test_local_mode_accepts_12_char_bootstrap_password():
    s = Settings(auth_mode="local", jwt_secret=STRONG, bootstrap_admin_password="x" * 12)
    assert s.bootstrap_admin_password == "x" * 12


def test_proxy_mode_ignores_bootstrap_password():
    # bootstrap_admin() is a no-op in proxy mode (spec §5.2), so a leftover
    # placeholder there is inert — demanding a change would be noise.
    s = Settings(
        auth_mode="proxy",
        proxy_auth_secret=STRONG,
        bootstrap_admin_password="bootstrap-admin-change-me",
    )
    assert s.auth_mode == "proxy"
