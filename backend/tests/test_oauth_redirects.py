"""Unit tests for oauth_redirects.py: where Google sign-in / Link Google may return to, in production
and in development. Pure functions, so no backend or database is needed."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from oauth_redirects import allowed_app_redirect, production_redirects  # noqa: E402

PROD = {"schemes": ("bookloop",), "origins": (), "dev": False}
PROD_WEB = {"schemes": ("bookloop",), "origins": ("https://app.example.com",), "dev": False}
DEV = {"schemes": ("bookloop",), "origins": (), "dev": True}


def test_production_accepts_exactly_the_app_return_screens():
    # What expo-linking's createURL("") / createURL("settings/account") produce in a store build.
    assert production_redirects(("bookloop",), ()) == frozenset({"bookloop://", "bookloop://settings/account"})
    assert allowed_app_redirect("bookloop://", **PROD)
    assert allowed_app_redirect("bookloop://settings/account", **PROD)


@pytest.mark.parametrize("url", [
    # Expo Go / dev servers: any host could be someone else's.
    "exp://evil.example/--/x", "exp://192.168.1.5:8081/--/", "exps://evil.example/",
    # localhost on the phone can be any app listening on that port.
    "http://localhost:8081/", "http://127.0.0.1:19006/x", "http://localhost/",
    # The app's scheme, but another host/path/query/fragment/credentials.
    "bookloop://auth", "bookloop://evil.example/", "bookloop:///", "bookloop://settings/account/",
    "bookloop://settings/accounts", "bookloop://?x=1", "bookloop://settings/account?a=1",
    "bookloop://settings/account#x", "bookloop://user@settings/account", "BOOKLOOP://",
    # Foreign targets.
    "https://evil.example/steal", "http://evil.example/", "javascript:alert(1)", "frontend://",
    "//evil.example", "http://user@evil.example/", "file:///etc/passwd", "data:text/html,x", "",
    "bookloop://" + "a" * 600,
])
def test_production_rejects_everything_else(url):
    assert not allowed_app_redirect(url, **PROD), url


def test_production_web_origin_exact_paths_only():
    assert allowed_app_redirect("https://app.example.com/", **PROD_WEB)
    assert allowed_app_redirect("https://app.example.com/settings/account", **PROD_WEB)
    for url in ("https://app.example.com", "https://app.example.com/x", "https://app.example.com/?a=1",
                "https://app.example.com.evil.example/", "http://app.example.com/",
                "https://user@app.example.com/", "https://app.example.com:444/"):
        assert not allowed_app_redirect(url, **PROD_WEB), url


@pytest.mark.parametrize("url", ["http://localhost:8081/", "http://127.0.0.1:19006/x", "exp://192.168.1.5:8081/--/",
                                 "exps://u.exp.direct/--/settings/account", "bookloop://", "bookloop://auth",
                                 "bookloop://192.168.1.5:8081/--/settings/account"])
def test_development_also_accepts_expo_go_dev_builds_and_localhost(url):
    assert allowed_app_redirect(url, **DEV)
    assert not allowed_app_redirect(url, **PROD) or url in ("bookloop://",)


@pytest.mark.parametrize("url", ["https://evil.example/", "http://evil.example/", "http://user@localhost/",
                                 "exp://user:pw@evil.example/", "javascript:alert(1)", "frontend://", "",
                                 "http://localhost.evil.example/", "http://[::1/"])
def test_development_still_rejects_foreign_targets(url):
    assert not allowed_app_redirect(url, **DEV), url


def test_app_scheme_kill_switch_leaves_only_web_origins():
    # server.py passes no schemes when GOOGLE_WEB_FLOW_APP_SCHEMES is off.
    off = {"schemes": (), "origins": ("https://app.example.com",), "dev": False}
    for url in ("bookloop://", "bookloop://settings/account"):
        assert not allowed_app_redirect(url, **off), url
    assert allowed_app_redirect("https://app.example.com/", **off)
    assert allowed_app_redirect("https://app.example.com/settings/account", **off)
