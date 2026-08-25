"""Parsers for Clutch.co content. Pure functions, no network.

Clutch publishes a native markdown rendering of every page for AI agents:
`/profile/{slug}.md`, `/{directory-path}.md`, and an `llms.txt` index. The markdown
is both smaller and better structured than the HTML (a profile is ~250 KB of
markdown against ~865 KB of HTML), and it carries data the HTML cards omit
entirely, so it is the primary source. HTML parsing exists only for the opt-in
`html` output format and as a fallback if the markdown layer changes shape.

Everything here is deliberately network-free so it can be unit-tested against
saved fixtures, following the same split as `ApifyWellfoundJobs`
(`parse_search_html` and friends are pure; only the client touches the wire).

Field coercion note: every value destined for a schema-`string` field goes
through `str_or_none`. A source field that flips int-to-string against a declared
string type fails dataset push validation with exit 91, which has cost this
portfolio live paid runs before.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

BASE_URL = "https://clutch.co"

# Tracking parameters Clutch appends to outbound provider links. Stripping these
# gives the caller the company's real homepage rather than a referral URL.
_TRACKING_PARAMS = ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term")

_PROFILE_PATH_RE = re.compile(r"^/profile/[A-Za-z0-9][A-Za-z0-9._-]*/?$")
_PCT_ITEM_RE = re.compile(r"^\s*-\s+(\d+(?:\.\d+)?)%\s+(.+?)\s*$")
_FOCUS_GROUP_RE = re.compile(r"^\s*-\s+([^:]+):\s*$")
_FOCUS_ITEM_RE = re.compile(r"^\s{2,}-\s+(\d+(?:\.\d+)?)%\s+(.+?)\s*$")
_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")


# ---------------------------------------------------------------------------
# Coercion helpers
# ---------------------------------------------------------------------------

def str_or_none(value: Any) -> str | None:
    """Coerce to a trimmed string, or None. Guards schema-`string` fields."""
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
    elif isinstance(value, bool):
        return "true" if value else "false"
    elif isinstance(value, (int, float)):
        text = str(value)
    else:
        text = str(value).strip()
    return text or None


def int_or_none(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = re.sub(r"[,\s]", "", str(value))
    m = re.search(r"-?\d+", text)
    if not m:
        return None
    try:
        return int(m.group(0))
    except ValueError:
        return None


def num_or_none(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    m = re.search(r"-?\d+(?:\.\d+)?", str(value).replace(",", ""))
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def collapse_ws(value: str | None) -> str | None:
    if not value:
        return None
    return re.sub(r"[ \t]+", " ", value).strip() or None


def strip_tracking(url: str | None) -> str | None:
    """Drop Clutch's utm_* referral parameters from an outbound link."""
    if not url:
        return None
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    if not parts.scheme or not parts.netloc:
        return None
    if parts.query:
        kept = [
            kv for kv in parts.query.split("&")
            if kv and kv.split("=", 1)[0].lower() not in _TRACKING_PARAMS
        ]
        parts = parts._replace(query="&".join(kept))
    return urlunsplit(parts)


# ---------------------------------------------------------------------------
# URL planning
# ---------------------------------------------------------------------------

def normalize_profile_url(value: str) -> str | None:
    """Accept a full profile URL or a bare slug; return the canonical URL."""
    text = (value or "").strip()
    if not text:
        return None
    if not text.startswith("http"):
        slug = text.strip("/")
        if not slug or "/" in slug:
            return None
        return f"{BASE_URL}/profile/{slug}"
    try:
        parts = urlsplit(text)
    except ValueError:
        return None
    host = (parts.netloc or "").lower().removeprefix("www.")
    if host != "clutch.co":
        return None
    path = (parts.path or "").removesuffix(".md")
    if not _PROFILE_PATH_RE.match(path):
        return None
    return f"{BASE_URL}{path.rstrip('/')}"


def normalize_directory_url(value: str) -> str | None:
    """Accept any clutch.co directory path (2-7 segments). Rejects profiles."""
    text = (value or "").strip()
    if not text:
        return None
    if not text.startswith("http"):
        text = f"{BASE_URL}/{text.lstrip('/')}"
    try:
        parts = urlsplit(text)
    except ValueError:
        return None
    host = (parts.netloc or "").lower().removeprefix("www.")
    if host != "clutch.co":
        return None
    path = (parts.path or "/").removesuffix(".md").rstrip("/")
    if not path or path.startswith("/profile/"):
        return None
    return f"{BASE_URL}{path}"


def markdown_url(url: str) -> str:
    """Map a canonical page URL to its `.md` twin."""
    base = url.removesuffix("/")
    return base if base.endswith(".md") else f"{base}.md"


def directory_page_url(url: str, page: int) -> str:
    """Nth page of a directory, as HTML.

    Verified 2026-08-20: the `.md` twin of a directory IGNORES `?page=`, serving
    the same 50 companies for every value, so directory pagination must use the
    HTML page. `?page=1` 301-redirects onto page 2, so page 0 is the bare URL and
    page N maps to `?page=N+1`.
    """
    base = url.removesuffix("/").removesuffix(".md")
    return base if page <= 0 else f"{base}?page={page + 1}"


def review_page_url(profile_url: str, page: int) -> str:
    """Review pagination on a profile.

    Verified 2026-08-20: the bare `?page=N` form 301-redirects, so the `.md`
    twin is used. Unlike directory pages, `.md?page=N` DOES paginate reviews.
    Page 0 and page 1 both return the first block of reviews, so callers should
    start paginating at 2.
    """
    base = profile_url.removesuffix("/").removesuffix(".md")
    return f"{base}.md" if page <= 0 else f"{base}.md?page={page}"


# ---------------------------------------------------------------------------
# Markdown section splitting
# ---------------------------------------------------------------------------

def _sections(lines: list[str], level: int) -> list[tuple[str, list[str]]]:
    """Split lines into (heading_text, body_lines) at exactly `level` hashes.

    Deeper headings stay inside the parent body; shallower ones end the scan.
    """
    out: list[tuple[str, list[str]]] = []
    current: tuple[str, list[str]] | None = None
    for line in lines:
        m = _HEADING_RE.match(line)
        if m:
            depth = len(m.group(1))
            if depth == level:
                if current:
                    out.append(current)
                current = (m.group(2).strip(), [])
                continue
            if depth < level:
                if current:
                    out.append(current)
                    current = None
                continue
        if current:
            current[1].append(line)
    if current:
        out.append(current)
    return out


def _percent_items(lines: Iterable[str]) -> list[dict]:
    """Parse `- 45% Web Design` bullets into {name, percent} records."""
    out = []
    for line in lines:
        m = _PCT_ITEM_RE.match(line)
        if m:
            out.append({"name": str_or_none(m.group(2)), "percent": num_or_none(m.group(1))})
    return out


def _focus_groups(lines: list[str]) -> list[dict]:
    """Parse the nested `- SEO Focus:` / `    - 30% On site optimization` shape."""
    groups: list[dict] = []
    current: dict | None = None
    for line in lines:
        item = _FOCUS_ITEM_RE.match(line)
        if item and current is not None:
            current["items"].append(
                {"name": str_or_none(item.group(2)), "percent": num_or_none(item.group(1))}
            )
            continue
        grp = _FOCUS_GROUP_RE.match(line)
        if grp and not _PCT_ITEM_RE.match(line):
            current = {"group": str_or_none(grp.group(1)), "items": []}
            groups.append(current)
    return [g for g in groups if g["items"]]


def _first_link(lines: Iterable[str], label_contains: str) -> str | None:
    needle = label_contains.lower()
    for line in lines:
        for label, href in _LINK_RE.findall(line):
            if needle in label.lower():
                return href
    return None


# ---------------------------------------------------------------------------
# Profile parsing
# ---------------------------------------------------------------------------

_RATING_LINE_RE = re.compile(r"([\d.]+)\s+out of\s+5\s+average review rating", re.I)
_REVIEWS_COUNT_RE = re.compile(r"Reviews\s*\((\d+)\)", re.I)
_CONNECTIONS_RE = re.compile(r"(\d[\d,]*)\s+connections", re.I)
_SEO_TITLE_RE = re.compile(r"Reviews\s*\(\d+\),\s*Pricing,\s*Services", re.I)
_VERIFICATION_TOKENS = ("premier verified", "verified", "clutch guarantee")


def parse_profile_markdown(text: str, url: str | None = None) -> dict:
    """Parse a `/profile/{slug}.md` body into one flat company record."""
    lines = (text or "").splitlines()
    record: dict[str, Any] = {"result_type": "profile"}
    if url:
        record["profile_url"] = normalize_profile_url(url) or url
        slug = (record["profile_url"] or "").rsplit("/", 1)[-1]
        record["slug"] = str_or_none(slug)

    # --- header block: everything before the first H2 ---
    head: list[str] = []
    for line in lines:
        if line.startswith("## "):
            break
        head.append(line)

    for line in head:
        m = _HEADING_RE.match(line)
        if m and len(m.group(1)) == 1:
            record["name"] = str_or_none(m.group(2))
            break

    head_text = "\n".join(head)
    if (m := _REVIEWS_COUNT_RE.search(head_text)):
        record["review_count"] = int_or_none(m.group(1))
    if (m := _RATING_LINE_RE.search(head_text)):
        record["rating"] = num_or_none(m.group(1))
    if (m := _CONNECTIONS_RE.search(head_text)):
        record["connections"] = int_or_none(m.group(1))

    badges = []
    for line in head:
        stripped = line.strip().lstrip("-").strip().lower()
        if stripped in _VERIFICATION_TOKENS:
            badges.append(str_or_none(line.strip().lstrip("-").strip()))
    record["verification"] = [b for b in badges if b]
    record["is_verified"] = bool(badges)

    record["website"] = strip_tracking(_first_link(head, "visit website"))

    # Description: prose after the link block, before the first H2. Line 2 of
    # every profile is the SEO title ("<name> Reviews (N), Pricing, ..."), which
    # is metadata rather than the company's own copy, so it is dropped.
    prose = [
        ln for ln in head
        if ln.strip()
        and not ln.startswith("#")
        and not ln.strip().startswith("- ")
        and not _SEO_TITLE_RE.search(ln)
        # Drop lines that are purely a markdown link (the CTA buttons).
        and not re.fullmatch(r"\s*\[[^\]]*\]\([^)]*\)\s*", ln)
    ]
    record["description"] = collapse_ws(" ".join(p.strip() for p in prose)) if prose else None

    h2 = dict(_sections(lines, 2))

    # --- Company Information ---
    if (info := h2.get("Company Information")):
        record.update(_parse_company_info(info))

    # --- Services / Focus / Industries / Clients ---
    svc_block = h2.get("Services, Focus Areas, Industries, and Clients", [])
    h3 = dict(_sections(svc_block, 3))
    record["service_lines"] = _percent_items(h3.get("Service Lines", []))
    record["focus_areas"] = _focus_groups(h3.get("Focus Areas", []))
    record["industries"] = _percent_items(h3.get("Industries", []))
    record["clients"] = _percent_items(h3.get("Clients", []))

    # --- Pricing ---
    if (pricing := h2.get("Pricing Snapshot")):
        record.update(_parse_pricing(pricing))

    # --- Reviews ---
    if (reviews := h2.get("Reviews")):
        record.update(_parse_review_section(reviews))
    else:
        record["reviews"] = []
        record["has_reviews"] = False

    return record


_LOC_HEADER_RE = re.compile(r"^\s*-\s+(\d+)\s+Locations?:", re.I)
_FOUNDED_RE = re.compile(r"^\s*-\s+Founded in\s+(\d{4})", re.I)
_LABELLED_RE = re.compile(r"^\s*-\s+([^:]+):\s*(.+?)\s*$")
_COUNT_LIST_RE = re.compile(r"^\s*-\s+(\d+)\s+(languages?|timezones?):\s*(.+?)\s*$", re.I)


def _parse_company_info(lines: list[str]) -> dict:
    out: dict[str, Any] = {"locations": []}
    in_locations = False
    for line in lines:
        if (m := _LOC_HEADER_RE.match(line)):
            in_locations = True
            out["location_count"] = int_or_none(m.group(1))
            continue
        if in_locations:
            if re.match(r"^\s{2,}-\s+", line):
                raw = line.strip().lstrip("-").strip()
                is_hq = "(headquarters)" in raw.lower()
                name = re.sub(r"\s*\(Headquarters\)\s*$", "", raw, flags=re.I)
                out["locations"].append(
                    {"location": str_or_none(name), "is_headquarters": is_hq}
                )
                continue
            if line.strip():
                in_locations = False
        if (m := _FOUNDED_RE.match(line)):
            out["founded_year"] = int_or_none(m.group(1))
            continue
        if (m := _COUNT_LIST_RE.match(line)):
            key = "languages" if m.group(2).lower().startswith("lang") else "timezones"
            out[key] = [str_or_none(v) for v in m.group(3).split(",") if str_or_none(v)]
            continue
        if (m := _LABELLED_RE.match(line)):
            label = m.group(1).strip().lower()
            value = str_or_none(m.group(2))
            if label.startswith("minimum project size"):
                out["min_project_size"] = value
            elif label.startswith("hourly rate"):
                out["hourly_rate"] = value
            elif label.startswith("number of employees"):
                out["employees"] = value

    if out["locations"]:
        hq = next((l for l in out["locations"] if l["is_headquarters"]), out["locations"][0])
        out["headquarters"] = hq.get("location")
    return out


_COST_RATING_RE = re.compile(r"Average rating for cost[^:]*:\s*([\d.]+)\s+out of\s+5", re.I)
_COMMON_SIZE_RE = re.compile(
    r"\*\*Most Common Project Size\*\*\s*:\s*(.+?)\s+based on\s+(\d[\d,]*)\s+reviews", re.I
)
# "- Search Engine Optimization: $50,000 to $199,999 based on 70 reviews"
_PRICE_SERVICE_RE = re.compile(
    r"^\s*-\s+(?P<svc>[^:]+):\s*(?P<range>.+?)\s+based on\s+(?P<n>\d[\d,]*)\s+reviews?", re.I
)


def _parse_pricing(lines: list[str]) -> dict:
    out: dict[str, Any] = {}
    blob = "\n".join(lines)
    if (m := _COST_RATING_RE.search(blob)):
        out["cost_rating"] = num_or_none(m.group(1))
    if (m := _COMMON_SIZE_RE.search(blob)):
        out["most_common_project_size"] = str_or_none(m.group(1))
        out["most_common_project_size_reviews"] = int_or_none(m.group(2))

    p3 = dict(_sections(lines, 3))
    out["pricing_by_service"] = [
        {
            "service": str_or_none(m.group("svc")),
            "project_size": str_or_none(m.group("range")),
            "review_count": int_or_none(m.group("n")),
        }
        for line in p3.get("Pricing by Service", [])
        if (m := _PRICE_SERVICE_RE.match(line))
    ]
    out["packages"] = [
        p for p in (
            str_or_none(ln.strip()[1:].strip())
            for ln in p3.get("Packages Offered", [])
            if ln.strip().startswith("-")
        ) if p
    ]
    return out


_SUBRATING_RE = re.compile(r"^\s*-\s+(Quality|Schedule|Cost|Willing to Refer):\s*([\d.]+)", re.I)
_OVERALL_RE = re.compile(r"Overall Review Rating:\s*([\d.]+)", re.I)
_MENTION_RE = re.compile(r"^\s*-\s+(.+?)\s+\((\d+)\s+mentions?\)", re.I)


def _parse_review_section(lines: list[str]) -> dict:
    out: dict[str, Any] = {}
    h3 = _sections(lines, 3)
    insights_body: list[str] = []
    mentions_body: list[str] = []
    reviews_body: list[str] = []
    for title, body in h3:
        low = title.lower()
        if low.endswith("review insights"):
            insights_body = body
        elif low == "top mentions":
            mentions_body = body
        elif low.endswith("reviews"):
            reviews_body = body

    blob = "\n".join(insights_body)
    if (m := _OVERALL_RE.search(blob)):
        out["review_rating_overall"] = num_or_none(m.group(1))
    subs: dict[str, float | None] = {}
    for line in insights_body:
        if (m := _SUBRATING_RE.match(line)):
            subs[m.group(1).strip().lower().replace(" ", "_")] = num_or_none(m.group(2))
    if subs:
        out["review_ratings"] = subs

    mentions = []
    for line in mentions_body:
        if (m := _MENTION_RE.match(line)):
            mentions.append({"mention": str_or_none(m.group(1)), "count": int_or_none(m.group(2))})
    out["top_mentions"] = mentions

    reviews = parse_reviews_markdown(reviews_body)
    out["reviews"] = reviews
    # The `## Reviews` heading is boilerplate and present even on 0-review
    # profiles, so presence is decided by actual entries, never the heading.
    out["has_reviews"] = bool(reviews)
    return out


_PROJECT_FIELD_RE = re.compile(r"^\s*-\s+(Services|Project size|Project length):\s*(.+?)\s*$", re.I)
_REVIEWER_FIELD_RE = re.compile(r"^\s*-\s+(Industry|Client size|Review Type):\s*(.+?)\s*$", re.I)
_BOLD_KV_RE = re.compile(r"^\*\*(.+?)\*\*\s*:?\s*(.*)$")
_REVIEW_DATE_RE = re.compile(r"\*\*The Review\*\*\s*[-—–]\s*(.+?)\s*$", re.M)
_RATING_KV_RE = re.compile(r"\*\*Review Rating\*\*\s*:\s*([\d.]+)", re.I)


def parse_reviews_markdown(lines: list[str] | str) -> list[dict]:
    """Parse `#### {title}` review blocks into structured review records."""
    if isinstance(lines, str):
        lines = lines.splitlines()
    out: list[dict] = []
    for title, body in _sections(lines, 4):
        rec: dict[str, Any] = {"result_type": "review"}
        featured = "(featured review)" in title.lower()
        rec["title"] = str_or_none(re.sub(r"\s*\(Featured Review\)\s*$", "", title, flags=re.I))
        rec["is_featured"] = featured

        blob = "\n".join(body)
        if (m := _RATING_KV_RE.search(blob)):
            rec["rating"] = num_or_none(m.group(1))
        if (m := _REVIEW_DATE_RE.search(blob)):
            rec["review_date"] = str_or_none(m.group(1))

        subs: dict[str, float | None] = {}
        for line in body:
            if (m := _SUBRATING_RE.match(line)):
                subs[m.group(1).strip().lower().replace(" ", "_")] = num_or_none(m.group(2))
            elif (m := _PROJECT_FIELD_RE.match(line)):
                # "Services" -> project_services; "Project size" -> project_size.
                key = m.group(1).strip().lower().replace(" ", "_")
                key = key if key.startswith("project_") else f"project_{key}"
                rec[key] = str_or_none(m.group(2))
            elif (m := _REVIEWER_FIELD_RE.match(line)):
                rec["reviewer_" + m.group(1).strip().lower().replace(" ", "_")] = \
                    str_or_none(m.group(2))
        if subs:
            rec["ratings"] = subs

        for key, target in (("Project Summary", "project_summary"),
                            ("Feedback Summary", "feedback_summary")):
            m = re.search(rf"\*\*{key}\*\*\s*:\s*(.+?)\s*$", blob, re.M)
            if m:
                rec[target] = collapse_ws(m.group(1))

        rec["reviewer_is_verified"] = bool(
            re.search(r"^\s*-\s+Verified\s*$", blob, re.M | re.I)
        )
        # The reviewer identity is the first non-empty line after **The Reviewer**.
        m = re.search(r"\*\*The Reviewer\*\*\s*\n+([^\n-][^\n]*)", blob)
        if m:
            rec["reviewer"] = collapse_ws(m.group(1))
        out.append(rec)
    # Clutch renders the featured review twice: once at the top under a
    # "(Featured Review)" heading and again in its date-ordered position. Keep
    # the first occurrence so counts match the profile's declared review total.
    deduped: list[dict] = []
    seen: set[tuple] = set()
    for rec in out:
        key = (rec.get("title"), rec.get("review_date"), rec.get("reviewer"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(rec)
    return deduped


# ---------------------------------------------------------------------------
# Directory parsing
# ---------------------------------------------------------------------------

_LISTING_HEAD_RE = re.compile(r"^\[(?P<name>.+?)\]\((?P<url>[^)]+)\)\s*$")
_STAR_RE = re.compile(r"([\d.]+)\s+out of\s+5\s+star rating\s*\((\d[\d,]*)\s+reviews?\)", re.I)
_MIN_SIZE_RE = re.compile(r"^\s*-\s+(.+?)\s+minimum project size\s*$", re.I)
_EMPLOYEES_RE = re.compile(r"^\s*-\s+(.+?)\s+employees\s*$", re.I)
# Listing meta bullets appear in a fixed order, with the hourly rate optional:
#   - $25,000+ minimum project size
#   - $100 - $149 average hourly rate      (may be absent)
#   - 50 - 249 employees
#   - New York, NY                          (residual bullet = location)
_HOURLY_RE = re.compile(r"^\s*-\s+(.+?)\s+(?:average hourly rate|/\s*hr)\s*$", re.I)


def parse_directory_markdown(text: str, url: str | None = None) -> list[dict]:
    """Parse a directory `.md` body into one record per listed company."""
    lines = (text or "").splitlines()
    out: list[dict] = []
    for heading, body in _sections(lines, 3):
        m = _LISTING_HEAD_RE.match(heading.strip())
        if not m:
            continue
        profile_url = normalize_profile_url(m.group("url"))
        if not profile_url:
            continue
        rec: dict[str, Any] = {
            "result_type": "listing",
            "name": str_or_none(m.group("name")),
            "profile_url": profile_url,
            "slug": str_or_none(profile_url.rsplit("/", 1)[-1]),
        }
        if url:
            rec["source_url"] = url

        h4 = _sections(body, 4)
        # The rating heading is the first H4 and carries the meta bullets.
        for h_title, h_body in h4:
            if (sm := _STAR_RE.search(h_title)):
                rec["rating"] = num_or_none(sm.group(1))
                rec["review_count"] = int_or_none(sm.group(2))
                for line in h_body:
                    if (x := _MIN_SIZE_RE.match(line)):
                        rec["min_project_size"] = str_or_none(x.group(1))
                    elif (x := _EMPLOYEES_RE.match(line)):
                        rec["employees"] = str_or_none(x.group(1))
                    elif (x := _HOURLY_RE.match(line)):
                        rec["hourly_rate"] = str_or_none(x.group(1))
                    elif line.strip().startswith("- ") and "location" not in rec:
                        # Residual bullet, after the three labelled ones, is location.
                        rec["location"] = str_or_none(line.strip()[2:])
            elif h_title.lower().startswith("services provided"):
                rec["service_lines"] = _percent_items(h_body)
            elif h_title.lower().startswith("company description"):
                desc = [
                    ln for ln in h_body
                    if ln.strip() and not re.fullmatch(r"\s*\[[^\]]*\]\([^)]*\)\s*", ln)
                ]
                rec["description"] = collapse_ws(" ".join(d.strip() for d in desc))
                rec["website"] = strip_tracking(_first_link(h_body, "visit website"))

        rec.setdefault("service_lines", [])
        out.append(rec)
    return out


# ---------------------------------------------------------------------------
# HTML parsing (opt-in `html` format / markdown-drift fallback)
# ---------------------------------------------------------------------------

_JSONLD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S | re.I
)
_CHARTPIE_RE = re.compile(r"chartPie\s*=\s*(\{.*?\})\s*;?\s*(?:\n|</script>)", re.S)


def extract_jsonld(html: str) -> list[dict]:
    """Return every parseable JSON-LD block in the page."""
    out = []
    for raw in _JSONLD_RE.findall(html or ""):
        try:
            parsed = json.loads(raw.strip())
        except (ValueError, TypeError):
            continue
        out.extend(parsed if isinstance(parsed, list) else [parsed])
    return [b for b in out if isinstance(b, dict)]


def parse_profile_html(html: str, url: str | None = None) -> dict:
    """Extract the LocalBusiness JSON-LD and the chartPie globals from a profile.

    `sameAs` carries the provider's real website, so the `r.clutch.co/redirect`
    tracker never has to be unwound.
    """
    rec: dict[str, Any] = {"result_type": "profile"}
    if url:
        rec["profile_url"] = normalize_profile_url(url) or url

    biz = next(
        (b for b in extract_jsonld(html)
         if str(b.get("@type", b.get("type", ""))).lower() == "localbusiness"),
        None,
    )
    if biz:
        rec["name"] = str_or_none(biz.get("name"))
        rec["description"] = collapse_ws(str_or_none(biz.get("description")))
        rec["phone"] = str_or_none(biz.get("telephone"))
        rec["founded_year"] = int_or_none(biz.get("foundingDate"))
        same = biz.get("sameAs")
        same_list = same if isinstance(same, list) else ([same] if same else [])
        rec["website"] = strip_tracking(str_or_none(same_list[0])) if same_list else None
        agg = biz.get("aggregateRating") or {}
        if isinstance(agg, dict):
            rec["rating"] = num_or_none(agg.get("ratingValue"))
            rec["review_count"] = int_or_none(agg.get("reviewCount"))
        addr = biz.get("address") or {}
        if isinstance(addr, dict):
            rec["address"] = {
                "country": str_or_none(addr.get("addressCountry")),
                "locality": str_or_none(addr.get("addressLocality")),
                "region": str_or_none(addr.get("addressRegion")),
                "postal_code": str_or_none(addr.get("postalCode")),
                "street": str_or_none(addr.get("streetAddress")),
            }

    if (m := _CHARTPIE_RE.search(html or "")):
        try:
            charts = json.loads(m.group(1))
        except (ValueError, TypeError):
            charts = {}
        for key, target in (("service_provided", "service_lines"),
                            ("industries", "industries"),
                            ("clients", "clients"),
                            ("focus", "focus_areas")):
            group = charts.get(key) or {}
            slices = group.get("slices") if isinstance(group, dict) else None
            if slices:
                rec[target] = [
                    {"name": str_or_none(s.get("name")),
                     "percent": num_or_none(s.get("PercentHundreds", s.get("percent")))}
                    for s in slices if isinstance(s, dict)
                ]
    return rec


# --- directory HTML (pagination path) --------------------------------------
# The `.md` twin of a directory page IGNORES `?page=`: every page returns the
# same 50 companies (verified 2026-08-20, name sets identical). Only the HTML
# page paginates, so directory mode reads HTML and parses these class hooks.
# Anchored on the <h3 class="provider__title"> wrapper. Anchoring on the
# `provider__title-link` class alone false-matches that string inside inline
# JavaScript. The href is absolute for organic listings and a PPC redirect for
# sponsored ones, so the profile URL is recovered from the card body when the
# heading href is not itself a profile link.
_CARD_TITLE_RE = re.compile(
    r'<h3[^>]*class="[^"]*provider__title\b[^"]*"[^>]*>\s*'
    r'<a[^>]*?href="(?P<href>[^"]*)"[^>]*>(?P<name>.*?)</a>',
    re.S,
)
_PROFILE_HREF_RE = re.compile(r'href="(?:https://clutch\.co)?(/profile/[A-Za-z0-9][A-Za-z0-9._-]*)')
_TAG_END = r'(?:[^>"]|"[^"]*")*>'
_HIGHLIGHT_RE = re.compile(
    r'class="[^"]*provider__highlights-item[^"]*?\b(?P<kind>min-project-size|hourly-rate|'
    r'employees-count|location)"' + _TAG_END + r'(?P<body>.*?)</div>',
    re.S,
)
_RATING_NUM_RE = re.compile(r'class="[^"]*sg-rating__number[^"]*"' + _TAG_END + r'(.*?)</span>', re.S)
_RATING_REV_RE = re.compile(r'class="[^"]*sg-rating__reviews[^"]*"' + _TAG_END + r'(.*?)</a>', re.S)
_VERIFIED_RE = re.compile(r'class="[^"]*provider__verified-mark[^"]*"' + _TAG_END + r'(.*?)</span>', re.S)
_TAG_RE = re.compile(r"<[^>]+>")


def _text(html: str | None) -> str | None:
    """Strip tags and entities from a markup fragment."""
    if not html:
        return None
    import html as _html
    return collapse_ws(_html.unescape(_TAG_RE.sub(" ", html)))


def parse_directory_html(html: str, url: str | None = None) -> list[dict]:
    """Parse a directory HTML page into one record per listed company.

    Used for every directory page because it is the only form that paginates.
    """
    text = html or ""
    matches = list(_CARD_TITLE_RE.finditer(text))
    out: list[dict] = []
    seen: set[str] = set()
    for idx, m in enumerate(matches):
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        window = text[m.end():end]

        # Organic listings link straight to the profile; sponsored ones point at
        # a PPC redirect, so fall back to the profile link inside the card.
        href = m.group("href") or ""
        path = _PROFILE_HREF_RE.search(f'href="{href}"') or _PROFILE_HREF_RE.search(window)
        profile_url = normalize_profile_url(f"{BASE_URL}{path.group(1)}") if path else None
        if not profile_url or profile_url in seen:
            continue
        seen.add(profile_url)

        rec: dict[str, Any] = {
            "result_type": "listing",
            "name": _text(m.group("name")),
            "profile_url": profile_url,
            "slug": str_or_none(profile_url.rsplit("/", 1)[-1]),
        }
        if url:
            rec["source_url"] = url
        if (r := _RATING_NUM_RE.search(window)):
            rec["rating"] = num_or_none(_text(r.group(1)))
        if (r := _RATING_REV_RE.search(window)):
            rec["review_count"] = int_or_none(_text(r.group(1)))
        badges = [b for b in (_text(x.group(1)) for x in _VERIFIED_RE.finditer(window)) if b]
        rec["verification"] = badges
        rec["is_verified"] = bool(badges)
        for h in _HIGHLIGHT_RE.finditer(window):
            value = _text(h.group("body"))
            if not value:
                continue
            kind = h.group("kind")
            if kind == "min-project-size":
                rec["min_project_size"] = value
            elif kind == "hourly-rate":
                rec["hourly_rate"] = str_or_none(re.sub(r"\s*/\s*hr\s*$", "", value, flags=re.I))
            elif kind == "employees-count":
                rec["employees"] = value
            elif kind == "location":
                rec["location"] = value
        rec.setdefault("service_lines", [])
        out.append(rec)
    return out


_SEARCH_HREF_RE = re.compile(r'href=["\'](/profile/[A-Za-z0-9][A-Za-z0-9._-]*)["\']')


def search_url(query: str) -> str:
    """Keyword search endpoint. There is no `.md` twin for /search (404), so
    this path is HTML-only and yields profile URLs to feed the profile pipeline."""
    from urllib.parse import quote_plus
    return f"{BASE_URL}/search?q={quote_plus((query or '').strip())}"


def parse_search_html(html: str) -> list[str]:
    """Extract unique, order-preserving profile URLs from a search results page."""
    seen: set[str] = set()
    out: list[str] = []
    for path in _SEARCH_HREF_RE.findall(html or ""):
        url = normalize_profile_url(f"{BASE_URL}{path}")
        if url and url not in seen:
            seen.add(url)
            out.append(url)
    return out


@dataclass
class PageResult:
    """One fetched-and-parsed page. Mirrors Martindale's SearchResult envelope."""
    label: str
    records: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
