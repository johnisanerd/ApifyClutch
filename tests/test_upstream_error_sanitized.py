"""Error-text sanitization tests.

This Actor has no upstream data vendor, so there is no vendor brand to hide.
The leak surface is different but just as real:

  1. HTTP client exceptions embed the FULL request URL (query strings, redirect
     chains) in their message. `str(exc)` reaching a log line or a dataset row
     would publish internal request detail on a public run.
  2. When a proxy tier is used, the proxy URL carries credentials
     (`http://user:pass@host:port`). That must never surface anywhere.
  3. A blocked or failed response body must not be echoed back to the caller.

Every one of these fails only at runtime while a static grep stays green, which
is exactly why these assertions exist.
"""
from __future__ import annotations

import asyncio
import importlib.util
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
SRC = HERE.parent / "ApifyClutch" / "src"

failures: list[str] = []
checks = 0


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, SRC / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


F = load("fetcher")

# Secrets and internal detail that must never appear in customer-visible text.
SECRET_USER = "proxyuser"
SECRET_PASS = "s3cr3t-token"
PROXY_URL = f"http://{SECRET_USER}:{SECRET_PASS}@proxy.internal.example:8000"
PRIVATE_URL = "https://clutch.co/profile/acme?session=abc123&token=zzz"

FORBIDDEN = (
    SECRET_PASS, SECRET_USER, "proxy.internal.example",
    "session=abc123", "token=zzz", "?session", "Traceback",
    "curl_cffi", "AsyncSession", "apify_proxy",
)


def check(label: str, condition: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if not condition:
        failures.append(f"{label}{(' — ' + detail) if detail else ''}")


def assert_clean(label: str, text: str | None) -> None:
    """No secret, credential, or raw-URL fragment may appear in customer text."""
    body = text or ""
    hits = [token for token in FORBIDDEN if token.lower() in body.lower()]
    check(label, not hits, f"leaked {hits!r} in {body!r}")


# ---------------------------------------------------------------------------
# 1. describe_error consults the exception TYPE only, never its message.
# ---------------------------------------------------------------------------
class FakeTimeout(Exception):
    pass


FakeTimeout.__name__ = "ConnectTimeout"


class FakeConnErr(Exception):
    pass


FakeConnErr.__name__ = "ProxyConnectionError"


class FakeReqErr(Exception):
    pass


FakeReqErr.__name__ = "RequestException"

# Each exception message is loaded with exactly what must not escape.
loaded = f"failed to reach {PRIVATE_URL} via {PROXY_URL}: Traceback curl_cffi"
for exc in (FakeTimeout(loaded), FakeConnErr(loaded), FakeReqErr(loaded),
            ValueError(loaded)):
    text = F.describe_error(exc)
    assert_clean(f"describe_error({type(exc).__name__}) is clean", text)
    check(f"describe_error({type(exc).__name__}) returns a phrase", bool(text))

check("timeout maps to the timeout phrase",
      F.describe_error(FakeTimeout(loaded)) == F.ERR_TIMEOUT)
check("proxy error maps to the connect phrase",
      F.describe_error(FakeConnErr(loaded)) == F.ERR_CONNECT)
check("unknown exception maps to the generic phrase",
      F.describe_error(ValueError(loaded)) == F.ERR_UNEXPECTED)


# ---------------------------------------------------------------------------
# 2. describe_status never echoes a response body.
# ---------------------------------------------------------------------------
for status in (400, 401, 403, 404, 429, 500, 503, 418):
    text = F.describe_status(status)
    assert_clean(f"describe_status({status}) is clean", text)
    check(f"describe_status({status}) returns a phrase", bool(text))
check("403 maps to the blocked phrase", F.describe_status(403) == F.ERR_BLOCKED)
check("404 says not found", "not found" in F.describe_status(404))


# ---------------------------------------------------------------------------
# 3. A failed fetch returns no body and a sanitized error, even when the
#    transport raises with a loaded message and a credentialed proxy is set.
# ---------------------------------------------------------------------------
class _Boom:
    """Stands in for curl_cffi.requests.AsyncSession and always raises."""

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, *a, **k):
        raise FakeConnErr(loaded)


async def _run_failure_path():
    fetcher = F.ClutchFetcher(
        proxy_urls={"direct": None, "residential": PROXY_URL},
        max_attempts=1,
    )
    import types
    fake = types.ModuleType("curl_cffi.requests")
    fake.AsyncSession = _Boom
    sys.modules["curl_cffi.requests"] = fake
    try:
        return await fetcher.fetch(PRIVATE_URL)
    finally:
        sys.modules.pop("curl_cffi.requests", None)


result = asyncio.run(_run_failure_path())
check("failed fetch is not ok", result.ok is False)
check("failed fetch carries no body", result.text == "", repr(result.text[:40]))
assert_clean("failed fetch error text is clean", result.error)
check("failed fetch still names a tier", bool(result.tier))


# ---------------------------------------------------------------------------
# 4. A challenge response must not echo the challenge body back.
# ---------------------------------------------------------------------------
class _Challenged(_Boom):
    async def get(self, *a, **k):
        class R:
            status_code = 403
            text = f"<title>Just a moment...</title> {PRIVATE_URL} {PROXY_URL}"
            headers = {"cf-mitigated": "challenge"}
        return R()


async def _run_challenge_path():
    fetcher = F.ClutchFetcher(proxy_urls={"direct": None}, max_attempts=1)
    import types
    fake = types.ModuleType("curl_cffi.requests")
    fake.AsyncSession = _Challenged
    sys.modules["curl_cffi.requests"] = fake
    try:
        return await fetcher.fetch(PRIVATE_URL)
    finally:
        sys.modules.pop("curl_cffi.requests", None)


challenged = asyncio.run(_run_challenge_path())
check("challenged fetch is not ok", challenged.ok is False)
check("challenged fetch drops the body", challenged.text == "",
      repr(challenged.text[:60]))
assert_clean("challenged fetch error text is clean", challenged.error)


# ---------------------------------------------------------------------------
# 5. The constant phrases themselves must be customer-safe.
# ---------------------------------------------------------------------------
for name in dir(F):
    if name.startswith("ERR_"):
        assert_clean(f"{name} is customer-safe", getattr(F, name))


# ---------------------------------------------------------------------------
# 6. Source-level guard: no call site may interpolate a raw exception or a
#    proxy URL into user-visible text. This is the static half of the check.
# ---------------------------------------------------------------------------
fetcher_src = (SRC / "fetcher.py").read_text()
main_src = (SRC / "main.py").read_text()

# Checked with the AST rather than a substring scan: prose in a docstring and a
# dict literal like {"https": proxy} are both legitimate, and a naive grep
# flags them while missing the thing that actually matters, which is an
# exception or a credentialed proxy URL being interpolated into a message.
import ast

_SENSITIVE_NAMES = {"exc", "e", "proxy", "proxy_url", "err"}


def interpolated_names(source: str) -> set[str]:
    """Every bare name interpolated into an f-string anywhere in the module."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.JoinedStr):
            for part in node.values:
                if isinstance(part, ast.FormattedValue):
                    for sub in ast.walk(part.value):
                        if isinstance(sub, ast.Name):
                            found.add(sub.id)
    return found


def calls_str_on(source: str, names: set[str]) -> bool:
    """True if `str(<name>)` is called on any of `names`."""
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "str" and node.args
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id in names):
            return True
    return False


fetcher_interp = interpolated_names(fetcher_src) & _SENSITIVE_NAMES
check("fetcher never interpolates an exception or proxy into a message",
      not fetcher_interp, f"interpolates {fetcher_interp!r}")
check("fetcher never calls str() on an exception or proxy",
      not calls_str_on(fetcher_src, _SENSITIVE_NAMES))
# main.py pushes res.error (already sanitized), never res.text, on a failure.
check("main pushes sanitized errors, not raw bodies",
      "res.error" in main_src and 'error_message": res.text' not in main_src)
check("main never logs a proxy URL",
      "proxy_url" not in main_src.replace("proxy_urls", ""))


# ---------------------------------------------------------------------------
print(f"ran {checks} checks")
if failures:
    print(f"FAIL: {len(failures)} check(s) failed:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("PASS: all error-sanitization checks passed")
