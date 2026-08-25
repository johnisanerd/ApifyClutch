"""Offline parser tests. No network, no API key, no Apify SDK.

Every assertion runs against fixtures captured from the live site on
2026-08-20, so a change in Clutch's markup or markdown shape fails here rather
than on a paid run.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
SRC = HERE.parent / "ApifyClutch" / "src"
FIXTURES = HERE / "fixtures"

failures: list[str] = []
checks = 0


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, SRC / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


C = load("clutch")
F = load("fetcher")


def check(label: str, condition: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if not condition:
        failures.append(f"{label}{(' — ' + detail) if detail else ''}")


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


# ---------------------------------------------------------------------------
# 1. URL planning
# ---------------------------------------------------------------------------
check("profile URL from full URL",
      C.normalize_profile_url("https://clutch.co/profile/ignite-visibility")
      == "https://clutch.co/profile/ignite-visibility")
check("profile URL from bare slug",
      C.normalize_profile_url("ignite-visibility")
      == "https://clutch.co/profile/ignite-visibility")
check("profile URL strips .md",
      C.normalize_profile_url("https://clutch.co/profile/foo.md")
      == "https://clutch.co/profile/foo")
check("profile URL rejects other hosts",
      C.normalize_profile_url("https://example.com/profile/foo") is None)
check("profile URL rejects a directory path",
      C.normalize_profile_url("https://clutch.co/web-developers") is None)
check("directory URL rejects a profile",
      C.normalize_directory_url("https://clutch.co/profile/foo") is None)
check("directory URL accepts a deep path",
      C.normalize_directory_url("https://clutch.co/us/agencies/ppc/chicago")
      == "https://clutch.co/us/agencies/ppc/chicago")
check("markdown_url appends .md",
      C.markdown_url("https://clutch.co/profile/foo") == "https://clutch.co/profile/foo.md")

# Directory `.md` ignores ?page=, so pagination must use HTML, and ?page=1
# redirects onto page 2 — page N therefore maps to ?page=N+1.
check("directory page 0 is the bare URL",
      C.directory_page_url("https://clutch.co/web-developers", 0)
      == "https://clutch.co/web-developers")
check("directory page 1 maps to ?page=2",
      C.directory_page_url("https://clutch.co/web-developers", 1)
      == "https://clutch.co/web-developers?page=2")
check("review pagination uses the .md twin",
      C.review_page_url("https://clutch.co/profile/foo", 2)
      == "https://clutch.co/profile/foo.md?page=2")

check("tracking params are stripped",
      C.strip_tracking("https://x.com/a?utm_source=clutch.co&utm_medium=referral&keep=1")
      == "https://x.com/a?keep=1")
check("non-URL tracking input is rejected",
      C.strip_tracking("not a url") is None)


# ---------------------------------------------------------------------------
# 2. Profile markdown
# ---------------------------------------------------------------------------
prof = C.parse_profile_markdown(fixture("profile.md"),
                                "https://clutch.co/profile/ignite-visibility")
check("profile name", prof.get("name") == "Ignite Visibility", repr(prof.get("name")))
check("profile slug", prof.get("slug") == "ignite-visibility")
check("profile rating", prof.get("rating") == 4.8, repr(prof.get("rating")))
check("profile review count", isinstance(prof.get("review_count"), int)
      and prof["review_count"] > 100)
check("profile is verified", prof.get("is_verified") is True)
check("profile website has no tracking",
      prof.get("website") and "utm_" not in prof["website"], repr(prof.get("website")))
check("profile min project size", prof.get("min_project_size") == "$1,000+")
check("profile hourly rate", prof.get("hourly_rate") == "$100 - $149")
check("profile employees", prof.get("employees") == "250 - 999")
check("profile founded year", prof.get("founded_year") == 2013)
check("profile headquarters", prof.get("headquarters") == "San Diego, CA")
check("profile locations parsed", len(prof.get("locations") or []) == 5)
check("headquarters flagged exactly once",
      sum(1 for x in prof["locations"] if x["is_headquarters"]) == 1)
check("languages parsed", len(prof.get("languages") or []) == 9)

# The SEO title line is metadata, not the company's own copy.
check("description excludes the SEO title line",
      prof.get("description") and "Pricing, Services & Verified Ratings" not in prof["description"],
      repr((prof.get("description") or "")[:60]))

check("service lines sum to 100",
      abs(sum(x["percent"] for x in prof["service_lines"]) - 100) < 0.01,
      str(sum(x["percent"] for x in prof["service_lines"])))
check("focus areas are grouped",
      any(g["group"] == "SEO Focus" and g["items"] for g in prof.get("focus_areas") or []))
check("industries parsed", len(prof.get("industries") or []) >= 10)
check("client sizes parsed", len(prof.get("clients") or []) == 3)

check("cost rating", prof.get("cost_rating") == 4.7)
check("most common project size",
      prof.get("most_common_project_size") == "$50,000 to $199,999")
check("pricing by service parsed", len(prof.get("pricing_by_service") or []) > 20)
check("pricing entry has service+size+count",
      all(x.get("service") and x.get("project_size") and isinstance(x.get("review_count"), int)
          for x in prof["pricing_by_service"]))
check("packages parsed", len(prof.get("packages") or []) == 4)
check("review rating breakdown",
      (prof.get("review_ratings") or {}).get("willing_to_refer") == 4.8)
check("top mentions parsed",
      len(prof.get("top_mentions") or []) > 5
      and all(isinstance(m["count"], int) for m in prof["top_mentions"]))


# ---------------------------------------------------------------------------
# 3. Reviews
# ---------------------------------------------------------------------------
reviews = prof.get("reviews") or []
check("reviews parsed", len(reviews) > 20, str(len(reviews)))
# Clutch renders the featured review twice; the parser must collapse it.
keys = [(r.get("title"), r.get("review_date"), r.get("reviewer")) for r in reviews]
check("reviews are de-duplicated", len(keys) == len(set(keys)),
      f"{len(keys)} rows vs {len(set(keys))} unique")
check("has_reviews is true when entries exist", prof.get("has_reviews") is True)

first = reviews[0]
check("review title", bool(first.get("title")))
check("featured flag is stripped from the title",
      "Featured Review" not in (first.get("title") or ""))
check("review rating is numeric", isinstance(first.get("rating"), float))
check("review sub-ratings", (first.get("ratings") or {}).get("quality") == 5.0)
check("review project size", bool(first.get("project_size")))
check("review project size is not double-prefixed", "project_project_size" not in first)
check("review project length", bool(first.get("project_length")))
check("reviewer identity", bool(first.get("reviewer")))
check("reviewer industry", bool(first.get("reviewer_industry")))
check("review date", bool(first.get("review_date")))
check("project summary", bool(first.get("project_summary")))

# A 0-review profile still renders the boilerplate "## Reviews" heading, so
# presence must be decided by entries, never by the heading.
thin = C.parse_profile_markdown(
    "# Tiny Co\nTiny Co Reviews (0), Pricing, Services & Verified Ratings\n\n"
    "## Reviews\n\nClutch investigates each reviewer's identity.\n",
    "https://clutch.co/profile/tiny-co")
check("thin profile reports has_reviews=False", thin.get("has_reviews") is False)
check("thin profile still yields a name", thin.get("name") == "Tiny Co")


# ---------------------------------------------------------------------------
# 4. Directory HTML (the paginating path)
# ---------------------------------------------------------------------------
listings = C.parse_directory_html(fixture("directory_p2.html"),
                                  "https://clutch.co/web-developers?page=2")
check("directory listings parsed", len(listings) >= 8, str(len(listings)))
check("every listing has a name", all(x.get("name") for x in listings))
check("every listing has a profile URL",
      all((x.get("profile_url") or "").startswith("https://clutch.co/profile/")
          for x in listings))
check("listing profile URLs are unique within a page",
      len({x["profile_url"] for x in listings}) == len(listings))

# Clutch's tooltip attributes contain '>' characters; a naive tag-close pattern
# leaks that copy into the value.
contaminated = [
    x for x in listings
    for k in ("min_project_size", "hourly_rate", "employees", "location")
    if isinstance(x.get(k), str) and ('"' in x[k] or ">" in x[k])
]
check("no markup leaks into listing fields", not contaminated,
      repr(contaminated[:1]))
check("listing min project size looks like money",
      all("$" in (x.get("min_project_size") or "$") for x in listings))
check("listing hourly rate has no /hr suffix",
      all("/hr" not in (x.get("hourly_rate") or "") for x in listings))
check("listing ratings are numeric or absent",
      all(x.get("rating") is None or isinstance(x["rating"], float) for x in listings))
check("verification badges are clean text",
      all(all(len(b) < 60 for b in (x.get("verification") or [])) for x in listings))


# ---------------------------------------------------------------------------
# 5. Directory markdown (page 1 shape, kept as a drift canary)
# ---------------------------------------------------------------------------
md_listings = C.parse_directory_markdown(fixture("directory.md"),
                                         "https://clutch.co/web-developers")
check("directory markdown still parses", len(md_listings) >= 40, str(len(md_listings)))
check("markdown listing location is not a labelled bullet",
      not [x for x in md_listings
           if x.get("location") and (
               "employees" in x["location"] or "hourly rate" in x["location"]
               or "minimum project size" in x["location"])])


# ---------------------------------------------------------------------------
# 6. Profile HTML: JSON-LD and chartPie
# ---------------------------------------------------------------------------
html_rec = C.parse_profile_html(fixture("profile.html"),
                                "https://clutch.co/profile/ignite-visibility")
check("json-ld name", html_rec.get("name") == "Ignite Visibility")
check("json-ld phone", bool(html_rec.get("phone")))
check("json-ld address", (html_rec.get("address") or {}).get("region") == "CA")
# `sameAs` carries the real homepage, so the r.clutch.co redirect never needs
# unwinding.
check("json-ld website is the real homepage",
      "clutch.co" not in (html_rec.get("website") or ""), repr(html_rec.get("website")))
check("chartPie service lines", len(html_rec.get("service_lines") or []) >= 5)
check("chartPie industries", len(html_rec.get("industries") or []) >= 5)

search_urls = C.parse_search_html(
    '<a href="/profile/alpha"></a><a href="/profile/beta"></a><a href="/profile/alpha"></a>')
check("search html de-duplicates and preserves order",
      search_urls == ["https://clutch.co/profile/alpha", "https://clutch.co/profile/beta"])


# ---------------------------------------------------------------------------
# 7. Coercion guard: no schema-`string` field may survive as a non-string.
#    RealestateAU lost three live paid runs to exit 91 when a source field
#    flipped int/string against a declared string type.
# ---------------------------------------------------------------------------
check("str_or_none coerces an int", C.str_or_none(5) == "5")
check("str_or_none coerces a float", C.str_or_none(4.5) == "4.5")
check("str_or_none maps empty to None", C.str_or_none("   ") is None)
check("int_or_none handles thousands separators", C.int_or_none("1,234 reviews") == 1234)
check("int_or_none rejects text", C.int_or_none("none") is None)
check("num_or_none parses a decimal", C.num_or_none("4.8 out of 5") == 4.8)
check("bool is not silently numeric", C.int_or_none(True) is None)

STRING_FIELDS = ("name", "slug", "min_project_size", "hourly_rate", "employees",
                 "location", "headquarters", "description")
for rec in (prof, listings[0], first):
    for key in STRING_FIELDS:
        value = rec.get(key)
        check(f"{key} is a string or absent",
              value is None or isinstance(value, str), f"{key}={type(value).__name__}")


# ---------------------------------------------------------------------------
# 8. Challenge detection
# ---------------------------------------------------------------------------
class _H(dict):
    pass


check("403 is a challenge", F.looks_like_challenge(403, "", _H()) is True)
check("429 is a challenge", F.looks_like_challenge(429, "", _H()) is True)
check("cf-mitigated header is a challenge",
      F.looks_like_challenge(200, "ok", _H({"cf-mitigated": "challenge"})) is True)
check("interstitial body is a challenge",
      F.looks_like_challenge(200, "<title>Just a moment...</title>", _H()) is True)
# A 3xx on clutch.co is canonicalisation (?page=1 -> page 2), not a block.
check("301 is not a challenge", F.looks_like_challenge(301, "", _H()) is False)
check("a served page is not a challenge",
      F.looks_like_challenge(200, "# Ignite Visibility", _H()) is False)
# A page that merely mentions the header name must not false-match.
check("mentioning cf-mitigated in the body is not a challenge",
      F.looks_like_challenge(200, "docs about the cf-mitigated header", _H()) is False)


# ---------------------------------------------------------------------------
print(f"ran {checks} checks")
if failures:
    print(f"FAIL: {len(failures)} check(s) failed:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("PASS: all offline unit checks passed")
