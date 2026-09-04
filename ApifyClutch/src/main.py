"""Apify Actor entry point for the Clutch.co API.

Three modes. `directory` (the default) walks any Clutch directory or category
page and returns one row per listed company. `profiles` collects specific
company profiles in full, optionally with every client review. `search` runs a
keyword query and then collects the profiles it finds.

Profiles and reviews come from Clutch's own markdown rendering
(`/profile/{slug}.md`), which the site publishes for AI agents. It is smaller
than the HTML, better structured, and carries fields the HTML cards omit, so a
profile row can carry publish-ready `markdown` at no extra fetch. Raw `html` is
an opt-in format because it costs a second request.

Directory mode reads page 0 from the directory's `.md` twin (lighter and more
reliable, and it carries website and description the HTML cards omit) and deeper
pages from the paginating HTML, because the `.md` twin ignores `?page=`. Every
directory fetch escalates through the proxy tiers on a soft challenge. Directory
rows carry structured fields; `markdown` and `html` formats apply to profile rows.

Charges pay-per-event: `listing-scraped` per directory row, `profile-scraped`
per company profile, `review-scraped` per client review.
"""

from __future__ import annotations

# MCP origin compatibility shim - must run before any Apify SDK call that
# triggers run-origin validation (e.g. set_status_message in SDK 3.x). Without
# it, runs initiated via the hosted Apify MCP server (meta.origin='MCP') crash
# in pydantic validation. Rule #30. Idempotent and fully guarded.
from .origin_compat import patch_unknown_run_origins  # noqa: E402
patch_unknown_run_origins()

import asyncio
import datetime
from decimal import Decimal

from apify import Actor
from dotenv import load_dotenv

from .clutch import (
    directory_page_url,
    markdown_url,
    normalize_directory_url,
    normalize_profile_url,
    parse_directory_html,
    parse_directory_markdown,
    parse_profile_html,
    parse_profile_markdown,
    parse_reviews_markdown,
    parse_search_html,
    review_page_url,
    search_url,
)
from .fetcher import build_fetcher

load_dotenv()

# Budget arithmetic deliberately over-estimates unit cost relative to the
# charged price so the affordability calculation stays conservative.
LISTING_COST = Decimal("0.0004")
PROFILE_COST = Decimal("0.0028")
REVIEW_COST = Decimal("0.0004")

MAX_ITEMS = 10000            # hard per-run delivery cap
MAX_DIRECTORY_URLS = 100
MAX_PROFILE_URLS = 500
MAX_QUERIES = 20
MAX_PAGES_PER_DIRECTORY = 50
MAX_REVIEWS_PER_PROFILE = 2000
# Directory pages carry 70-91 companies (organic plus sponsored and featured
# blocks). The budget estimate deliberately uses the high end so a run never
# plans more pages than it can pay for.
LISTINGS_PER_PAGE = 90

CHUNK = 10                   # targets per chunk: push+charge before fetching more
CONCURRENCY = 5              # parallel fetches inside one chunk


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _clean(row: dict) -> dict:
    """Drop None-valued keys before pushing. The dataset schema validates pushed
    items at runtime, so a null value would fail validation; omitting is valid."""
    return {k: v for k, v in row.items() if v is not None}


async def _charge(event_name: str, count: int = 1) -> bool:
    """Charge an event. Returns True when the run's charge limit is reached, so
    the caller can stop pushing rows it cannot bill."""
    if not Actor.is_at_home() or count <= 0:
        return False
    try:
        result = await Actor.charge(event_name, count)
        return bool(getattr(result, "event_charge_limit_reached", False))
    except Exception as e:  # noqa: BLE001
        Actor.log.warning(f"Failed to charge '{event_name}' x{count}: {e}")
        return False


async def _fail(message: str, error_type: str) -> None:
    Actor.log.error(message)
    await Actor.push_data(_clean({
        "result_type": "error",
        "error_message": message,
        "error_type": error_type,
        "fetched_at": _now(),
    }))
    await Actor.set_status_message(message, is_terminal=True)


async def _push_error(label: str, message: str, error_type: str = "FetchError") -> None:
    await Actor.push_data(_clean({
        "result_type": "error",
        "source_url": label,
        "error_message": message,
        "error_type": error_type,
        "fetched_at": _now(),
    }))


def _formats(actor_input: dict) -> set[str]:
    raw = actor_input.get("outputFormats") or ["json", "markdown"]
    if isinstance(raw, str):
        raw = [raw]
    picked = {str(f).strip().lower() for f in raw if str(f).strip()}
    return picked & {"json", "markdown", "html"} or {"json", "markdown"}


def _budget_units(unit_cost: Decimal) -> int | None:
    """How many billable units this run can pay for, or None when uncapped."""
    if not Actor.is_at_home():
        return None
    try:
        max_budget = Actor.get_charging_manager().get_max_total_charge_usd()
    except Exception:  # noqa: BLE001
        Actor.log.warning("Could not read the run budget; proceeding uncapped by budget.")
        return None
    if max_budget is None:
        return None
    # No charge cap on this run (unmonetized actor, or a user with no spend
    # limit) comes back as Infinity. int(Infinity) raises, and a missing cap
    # simply means there is nothing to cap against.
    if not Decimal(str(max_budget)).is_finite():
        return None
    return int(Decimal(str(max_budget)) / unit_cost)


async def _run() -> None:  # noqa: C901
    actor_input = await Actor.get_input() or {}

    # 1. Validate and normalize input BEFORE touching anything else, so an
    #    empty-input run exits cleanly.
    mode = (actor_input.get("mode") or "directory").strip().lower()
    if mode not in ("directory", "profiles", "search"):
        await _fail(f"Unknown mode '{mode}'. Use 'directory', 'profiles', or 'search'.",
                    "InvalidInput")
        return

    formats = _formats(actor_input)
    include_reviews = bool(actor_input.get("includeReviews", True))
    max_items = max(1, min(int(actor_input.get("maxItems") or 1000), MAX_ITEMS))
    max_pages = max(1, min(int(actor_input.get("maxPagesPerDirectory") or 1),
                           MAX_PAGES_PER_DIRECTORY))
    max_reviews = max(1, min(int(actor_input.get("maxReviewsPerProfile") or 100),
                             MAX_REVIEWS_PER_PROFILE))

    directory_urls: list[str] = []
    profile_urls: list[str] = []
    queries: list[str] = []

    if mode == "directory":
        for raw in (actor_input.get("directoryUrls") or [])[:MAX_DIRECTORY_URLS]:
            value = raw.get("url") if isinstance(raw, dict) else raw
            if (norm := normalize_directory_url(str(value or ""))):
                directory_urls.append(norm)
        if not directory_urls:
            await _fail(
                "No valid Clutch directory URLs were provided. Give at least one URL like "
                "https://clutch.co/web-developers or https://clutch.co/us/agencies/digital-marketing.",
                "InvalidInput")
            return
    elif mode == "profiles":
        for raw in (actor_input.get("profileUrls") or [])[:MAX_PROFILE_URLS]:
            value = raw.get("url") if isinstance(raw, dict) else raw
            if (norm := normalize_profile_url(str(value or ""))):
                profile_urls.append(norm)
        if not profile_urls:
            await _fail(
                "No valid Clutch profile URLs were provided. Give at least one URL like "
                "https://clutch.co/profile/ignite-visibility (or just the slug).",
                "InvalidInput")
            return
    else:
        queries = [str(q).strip() for q in (actor_input.get("searchQueries") or [])
                   if str(q or "").strip()][:MAX_QUERIES]
        if not queries:
            await _fail("No search queries were provided. Give at least one keyword.",
                        "InvalidInput")
            return

    fetcher = await build_fetcher(Actor)
    total = 0
    stop = False

    async def fetch_many(urls: list[str]) -> list:
        """Fetch a chunk concurrently, preserving input order."""
        sem = asyncio.Semaphore(CONCURRENCY)

        async def one(u: str):
            async with sem:
                return await fetcher.fetch(u)
        return await asyncio.gather(*(one(u) for u in urls))

    # ------------------------------------------------------------------
    # directory mode
    # ------------------------------------------------------------------
    if mode == "directory":
        # Cap pagination depth BEFORE fetching: every page is a separate
        # request, so trimming after the fact would pay for rows never sold.
        affordable = _budget_units(LISTING_COST)
        page_budget = max_pages * len(directory_urls)
        if affordable is not None:
            affordable_pages = max(1, affordable // LISTINGS_PER_PAGE)
            if affordable_pages < page_budget:
                Actor.log.warning(
                    f"Run budget covers about {affordable_pages} directory page(s); "
                    f"{page_budget} were requested. Raise the budget or split the run.")
                page_budget = affordable_pages

        # Page 0 is read from the directory's `.md` twin: it is lighter than the
        # HTML, carries website and description fields the HTML cards omit, and
        # is the more reliable endpoint from the platform's shared egress.
        # Deeper pages have no working `.md` (the `.md` ignores ?page=), so they
        # fall back to the paginating HTML. Both carry a content marker so a
        # soft-challenged page escalates through the proxy tiers instead of
        # parsing to 0 rows.
        targets: list[dict] = []
        for url in directory_urls:
            for page in range(max_pages):
                if len(targets) >= page_budget:
                    break
                if page == 0:
                    targets.append({"url": url, "page": 0, "fetch": markdown_url(url),
                                    "expect": "### [", "kind": "md"})
                else:
                    targets.append({"url": url, "page": page,
                                    "fetch": directory_page_url(url, page),
                                    "expect": "provider__title-link", "kind": "html"})

        await Actor.set_status_message(
            f"Reading {len(targets)} directory page(s) from {len(directory_urls)} URL(s).")
        seen_companies: set[str | None] = set()

        async def fetch_dir_chunk(items: list[dict]) -> list:
            sem = asyncio.Semaphore(CONCURRENCY)

            async def one(t: dict):
                async with sem:
                    return t, await fetcher.fetch(t["fetch"], expect=t["expect"])
            return await asyncio.gather(*(one(t) for t in items))

        for start in range(0, len(targets), CHUNK):
            if stop:
                break
            chunk = targets[start:start + CHUNK]
            Actor.log.info(f"Chunk {start // CHUNK + 1}: {len(chunk)} directory page(s).")
            for tgt, res in await fetch_dir_chunk(chunk):
                if stop:
                    break
                if not res.ok:
                    await _push_error(tgt["url"], res.error or "the page could not be read")
                    continue
                rows = (parse_directory_markdown(res.text, tgt["url"]) if tgt["kind"] == "md"
                        else parse_directory_html(res.text, tgt["url"]))
                if not rows:
                    if tgt["page"] == 0:
                        # A real category page always lists companies. Zero rows
                        # on page 0 of a 200 that passed the marker check means
                        # the layout changed; surface it rather than hide it.
                        await _push_error(tgt["url"],
                                          "the directory page returned no companies",
                                          "EmptyDirectory")
                    else:
                        # A later page with nothing is the end of pagination.
                        Actor.log.info("No listings on this page; treating as end of results.")
                    continue
                for row in rows:
                    if total >= max_items:
                        stop = True
                        break
                    # Sponsored and featured cards repeat across pages, so the
                    # same company can appear on several. De-duplicate for the
                    # whole run: a caller must never be billed twice for one row.
                    key = row.get("profile_url")
                    if key in seen_companies:
                        continue
                    seen_companies.add(key)
                    await Actor.push_data(_clean({**row, "fetched_at": _now()}))
                    total += 1
                    if await _charge("listing-scraped", 1):
                        Actor.log.warning(
                            f"Charge limit reached after {total} listing(s). Stopping early.")
                        stop = True
                        break

    # ------------------------------------------------------------------
    # search mode -> resolve queries to profile URLs, then fall through
    # ------------------------------------------------------------------
    else:
        if mode == "search":
            await Actor.set_status_message(f"Searching {len(queries)} query/queries.")
            found: list[str] = []
            for res in await fetch_many([search_url(q) for q in queries]):
                if not res.ok:
                    await _push_error(res.url, res.error or "the search page could not be read")
                    continue
                found.extend(parse_search_html(res.text))
            # De-duplicate while preserving discovery order.
            seen: set[str] = set()
            profile_urls = [u for u in found if not (u in seen or seen.add(u))]
            if not profile_urls:
                await _fail("No company profiles matched those search queries.", "NoResults")
                return
            Actor.log.info(f"Search found {len(profile_urls)} profile(s).")

        per_profile_cost = PROFILE_COST + (REVIEW_COST * max_reviews if include_reviews
                                           else Decimal("0"))
        affordable = _budget_units(per_profile_cost)
        targets = profile_urls if affordable is None else profile_urls[:max(1, affordable)]
        if (skipped := len(profile_urls) - len(targets)) > 0:
            Actor.log.warning(
                f"Processing {len(targets)} of {len(profile_urls)} profile(s); {skipped} "
                f"skipped (run budget). Raise the budget or split the run.")

        await Actor.set_status_message(f"Collecting {len(targets)} company profile(s).")

        for start in range(0, len(targets), CHUNK):
            if stop:
                break
            chunk = targets[start:start + CHUNK]
            Actor.log.info(f"Chunk {start // CHUNK + 1}: {len(chunk)} profile(s).")
            results = await fetch_many([markdown_url(u) for u in chunk])

            for profile_url, res in zip(chunk, results):
                if stop:
                    break
                if not res.ok:
                    await _push_error(profile_url, res.error or "the profile could not be read")
                    continue

                row = parse_profile_markdown(res.text, profile_url)
                reviews = row.pop("reviews", []) or []

                # Raw HTML is a second request, so it is opt-in only.
                if "html" in formats:
                    html_res = await fetcher.fetch(profile_url)
                    if html_res.ok:
                        row["html"] = html_res.text
                        # JSON-LD carries phone and a full postal address, which
                        # the markdown does not expose.
                        for key, value in parse_profile_html(html_res.text, profile_url).items():
                            row.setdefault(key, value)
                        if not row.get("phone"):
                            row["phone"] = parse_profile_html(
                                html_res.text, profile_url).get("phone")
                if "markdown" in formats:
                    row["markdown"] = res.text

                if total >= max_items:
                    stop = True
                    break
                await Actor.push_data(_clean({**row, "fetched_at": _now()}))
                total += 1
                if await _charge("profile-scraped", 1):
                    Actor.log.warning(
                        f"Charge limit reached after {total} profile(s). Stopping early.")
                    stop = True
                    break

                if not include_reviews:
                    continue

                # Page 1 reviews already arrived with the profile; only paginate
                # when the caller asked for more than that page delivered.
                collected = list(reviews[:max_reviews])
                declared = row.get("review_count") or 0
                seen_reviews = {(r.get("title"), r.get("review_date")) for r in collected}
                # Pages 0 and 1 serve the same first block, so deeper paging
                # starts at 2.
                page = 2
                while (len(collected) < max_reviews and len(collected) < declared
                       and page <= MAX_PAGES_PER_DIRECTORY and not stop):
                    r = await fetcher.fetch(review_page_url(profile_url, page))
                    if not r.ok:
                        await _push_error(profile_url, r.error or "a review page could not be read")
                        break
                    fresh = [
                        rv for rv in parse_reviews_markdown(r.text)
                        if (rv.get("title"), rv.get("review_date")) not in seen_reviews
                    ]
                    if not fresh:
                        break
                    for rv in fresh:
                        seen_reviews.add((rv.get("title"), rv.get("review_date")))
                    collected.extend(fresh[:max_reviews - len(collected)])
                    page += 1

                for review in collected:
                    if total >= max_items:
                        stop = True
                        break
                    await Actor.push_data(_clean({
                        **review,
                        "company_name": row.get("name"),
                        "company_slug": row.get("slug"),
                        "profile_url": profile_url,
                        "fetched_at": _now(),
                    }))
                    total += 1
                    if await _charge("review-scraped", 1):
                        Actor.log.warning(
                            f"Charge limit reached after {total} row(s). Stopping early.")
                        stop = True
                        break

    await Actor.set_status_message(f"Done. {total} row(s) collected.", is_terminal=True)


async def main() -> None:
    async with Actor:
        try:
            await _run()
        finally:
            Actor.log.info(
                "Thanks for running this Actor. Support, docs, and more data "
                "sources: https://www.alphaosint.com"
            )
