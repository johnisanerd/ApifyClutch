"""HTTP transport for Clutch.co, with browser-grade TLS fingerprinting.

Clutch sits behind a Cloudflare managed challenge on every path: a plain Python
client is refused with `403` and `cf-mitigated: challenge` even on `robots.txt`.
What clears it is the TLS/HTTP2 handshake, not the originating address.

Measured 2026-08-20, 150 distinct profile pages fetched back-to-back with
`curl_cffi` impersonating Chrome and **no proxy at all**: 0 challenges, 100%
valid responses, 0.12s median latency. That is the same finding
`ApifyMapsPlaceContacts` recorded against Google (fingerprint matters, address
does not), and the same client `ApifyWellfoundJobs` already runs against
Cloudflare in production.

So the default tier is a direct connection. A proxy ladder remains as a fallback
for the case where Clutch tightens up, but it is deliberately NOT exposed in the
input schema: under pay-per-event the platform bills the developer, so a visible
residential toggle would let a caller spend roughly ten times the bandwidth at
no cost to themselves and gain nothing measurable.

Error-text discipline: HTTP client exceptions embed the full request URL, so a
raw `str(exc)` reaching a log line or a dataset row would publish internal
request detail. Every failure is mapped to a constant phrase AT THIS LAYER,
never at the call site.
"""
from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field

TIMEOUT_SECONDS = 40
MAX_ATTEMPTS = 3
IMPERSONATE = "chrome"

# Header set kept consistent with the impersonated TLS fingerprint. Adding
# unusual headers on top of an impersonation profile is a known way to make the
# handshake and the HTTP layer disagree, which reads as automation.
DEFAULT_HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "accept-language": "en-US,en;q=0.9",
    "upgrade-insecure-requests": "1",
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "none",
    "sec-fetch-user": "?1",
}

# Body markers of a real interstitial. The `cf-mitigated` signal is a response
# HEADER and is checked separately: scanning the body for that name false-matches
# served pages that merely mention it.
_CHALLENGE_MARKERS = ("just a moment", "captcha-delivery", "enable javascript and cookies")

ERR_TIMEOUT = f"the request timed out after {TIMEOUT_SECONDS} seconds"
ERR_CONNECT = "could not connect to the site"
ERR_BLOCKED = "the site declined to serve this page (anti-bot challenge)"
ERR_BAD_URL = "the URL could not be requested"
ERR_UNEXPECTED = "an unexpected error occurred while fetching the page"


class FetchError(RuntimeError):
    """A fetch failure, already phrased for the user. Never carries raw
    exception text or the request URL."""


def describe_status(status: int) -> str:
    """Customer-safe text for an HTTP status. Never echoes the response body."""
    if status == 404:
        return "that page was not found on the site"
    if status == 429:
        return "the site is rate limiting requests right now"
    if status in (401, 403):
        return ERR_BLOCKED
    if 500 <= status < 600:
        return "the site returned a server error"
    return f"the site returned HTTP {status}"


def describe_error(exc: Exception) -> str:
    """Map a client exception to a constant phrase.

    The exception is consulted only for its TYPE. Its message is deliberately
    discarded because `curl_cffi` and `requests` both build exception strings
    from the request URL, which would leak query strings and redirect chains
    into a public run log while every static grep stayed green.
    """
    name = type(exc).__name__.lower()
    if "timeout" in name:
        return ERR_TIMEOUT
    if "connection" in name or "proxy" in name or "dns" in name:
        return ERR_CONNECT
    if "request" in name or "http" in name:
        return ERR_BAD_URL
    return ERR_UNEXPECTED


def looks_like_challenge(status: int, body: str, headers=None) -> bool:
    """True if the response is a Cloudflare challenge or a block.

    Signals, in order: a blocking status code; the `cf-mitigated` response
    header (set to `challenge` on a managed challenge); or an interstitial
    marker in the page body.
    """
    if status in (403, 429):
        return True
    if headers is not None:
        try:
            if headers.get("cf-mitigated"):
                return True
        except Exception:
            pass
    head = (body or "")[:2000].lower()
    return any(marker in head for marker in _CHALLENGE_MARKERS)


@dataclass
class FetchResult:
    url: str
    status: int
    text: str
    ok: bool
    tier: str          # "direct" | "datacenter" | "residential"
    error: str | None = None


@dataclass
class ClutchFetcher:
    """Fetches Clutch pages, escalating tiers only if a challenge appears.

    `proxy_urls` maps a tier name to a proxy URL. The default is a single
    "direct" tier with no proxy, which Phase 0 measured at a 0% challenge rate.
    """
    proxy_urls: dict[str, str | None] = field(default_factory=lambda: {"direct": None})
    impersonate: str = IMPERSONATE
    timeout: int = TIMEOUT_SECONDS
    max_attempts: int = MAX_ATTEMPTS
    tier_order: tuple[str, ...] = ("direct", "datacenter", "residential")

    def _tiers(self) -> list[tuple[str, str | None]]:
        return [(t, self.proxy_urls[t]) for t in self.tier_order if t in self.proxy_urls]

    async def fetch(self, url: str) -> FetchResult:
        """Fetch one URL. Returns a FetchResult; never raises for HTTP problems."""
        from curl_cffi.requests import AsyncSession

        last: FetchResult | None = None
        for tier, proxy in self._tiers():
            proxies = {"http": proxy, "https": proxy} if proxy else None
            for attempt in range(1, self.max_attempts + 1):
                # Micro-jitter so bursts never land in lockstep.
                await asyncio.sleep(random.uniform(0.02, 0.08))
                try:
                    async with AsyncSession() as session:
                        resp = await session.get(
                            url,
                            headers=DEFAULT_HEADERS,
                            impersonate=self.impersonate,
                            proxies=proxies,
                            timeout=self.timeout,
                            # Clutch 301-redirects some paginated paths to their
                            # canonical form, so redirects are followed. A 3xx
                            # here is canonicalization, never a block.
                            allow_redirects=True,
                            max_redirects=5,
                        )
                    body = resp.text or ""
                    if not looks_like_challenge(resp.status_code, body, resp.headers):
                        if resp.status_code >= 400:
                            last = FetchResult(url, resp.status_code, "", False, tier,
                                               describe_status(resp.status_code))
                            # A 404 is definitive; escalating tiers cannot fix it.
                            if resp.status_code == 404:
                                return last
                        else:
                            return FetchResult(url, resp.status_code, body, True, tier)
                    else:
                        last = FetchResult(url, resp.status_code, "", False, tier, ERR_BLOCKED)
                except Exception as exc:
                    last = FetchResult(url, 0, "", False, tier, describe_error(exc))
                await asyncio.sleep(0.4 * attempt)
        return last or FetchResult(url, 0, "", False, "direct", ERR_UNEXPECTED)


async def build_fetcher(actor, use_proxy_fallback: bool = True) -> ClutchFetcher:
    """Construct a fetcher with a direct tier plus platform proxy fallbacks.

    Proxy credentials come from the platform at run time; there is no vendor
    API key anywhere in this actor.
    """
    tiers: dict[str, str | None] = {"direct": None}
    if use_proxy_fallback:
        for tier, groups in (("datacenter", None), ("residential", ["RESIDENTIAL"])):
            try:
                cfg = await actor.create_proxy_configuration(groups=groups)
                if cfg:
                    url = await cfg.new_url()
                    if url:
                        tiers[tier] = url
            except Exception:
                # No proxy entitlement is not an error: the direct tier is the
                # measured default and carries the run on its own.
                continue
    return ClutchFetcher(proxy_urls=tiers)
