"""Proxy-auth mode (spec 2026-08-27): settings, provisioning, resolver, routes."""

import pytest

from graphrag_ui.config import Settings


def test_proxy_mode_requires_32_char_secret():
    with pytest.raises(ValueError):
        Settings(auth_mode="proxy", proxy_auth_secret="short")


def test_proxy_mode_rejects_empty_secret():
    with pytest.raises(ValueError):
        Settings(auth_mode="proxy", proxy_auth_secret="")


def test_local_mode_allows_empty_secret():
    # Default deployments keep today's behavior: no secret needed (spec §4)
    assert Settings(auth_mode="local", proxy_auth_secret="").auth_mode == "local"


def test_proxy_mode_accepts_32_char_secret():
    s = Settings(auth_mode="proxy", proxy_auth_secret="x" * 32)
    assert s.auth_mode == "proxy"


def test_proxy_admin_set_lowercases_strips_and_dedupes():
    s = Settings(proxy_admin_emails="A@Ex.COM, b@ex.com , ,")
    assert s.proxy_admin_set == frozenset({"a@ex.com", "b@ex.com"})


def test_proxy_admin_set_empty_default():
    assert Settings().proxy_admin_set == frozenset()
