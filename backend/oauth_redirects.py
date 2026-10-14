"""Where the backend may send a browser back to after Google (sign-in or account linking).

Pure functions (no I/O), so they are unit-tested without a server (tests/test_oauth_redirects.py).

Production accepts EXACT return URLs only: the app's return screens under its own URL scheme(s), and
under any configured web origin. The return screens are what expo-linking's createURL() builds in a
store build: createURL("") -> "bookloop://" (sign-in), createURL("settings/account") ->
"bookloop://settings/account" (Settings -> Account -> Link Google). No other host, path, query,
fragment or credentials are accepted.

Development (ALLOW_DEV_REDIRECTS) additionally accepts Expo Go's exp:// / exps:// URLs, the app's own
scheme with any host/path (development builds put the dev server address in the URL), and
http://localhost / http://127.0.0.1 on any port. Never enable that on a backend reachable from the
internet: those targets can belong to someone other than the person signing in.
"""
from typing import Iterable
from urllib.parse import urlparse

# The app screens Google returns to (see the module docstring).
APP_RETURN_PATHS = ("", "settings/account")
DEV_SCHEMES = ("exp", "exps")
LOCAL_HOSTS = ("localhost", "127.0.0.1")
MAX_REDIRECT_CHARS = 500


def production_redirects(schemes: Iterable[str], origins: Iterable[str]) -> frozenset:
    """Every return URL production accepts, spelled out."""
    out = set()
    for path in APP_RETURN_PATHS:
        out.update(f"{s}://{path}" for s in schemes)
        out.update(f"{o}/{path}" for o in origins)
    return frozenset(out)


def allowed_app_redirect(url: str, *, schemes: Iterable[str], origins: Iterable[str], dev: bool = False) -> bool:
    if not url or len(url) > MAX_REDIRECT_CHARS:
        return False
    schemes = tuple(schemes)
    if url in production_redirects(schemes, origins):
        return True
    if not dev:
        return False
    try:
        p = urlparse(url)
        if p.username is not None or p.password is not None:
            return False
        hostname = p.hostname
    except ValueError:
        return False
    if p.scheme in DEV_SCHEMES or p.scheme in schemes:
        return True
    return p.scheme == "http" and hostname in LOCAL_HOSTS
