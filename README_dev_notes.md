# ApifyClutch — dev notes

Internal notes. Not published to the Store.

## Transport: no vendor, no proxy

Clutch.co sits behind a Cloudflare **managed challenge** on every path. A plain
Python client gets `403` with `cf-mitigated: challenge`, even on `robots.txt`.

What clears it is the TLS/HTTP2 handshake, not the originating address.
Measured 2026-08-20, `curl_cffi` impersonating Chrome, **no proxy at all**:

| Metric | Value |
|---|---|
| Requests | 150 distinct profiles, back-to-back |
| Cloudflare challenges | **0** |
| Latency | 0.12s median, 0.17s/req sustained |
| Body size | p50 3.6 KB, p90 36.8 KB, max 217 KB |
| Egress cost | ~$0.0000087 per profile @ $0.60/GB |

This is the same result `ApifyMapsPlaceContacts` recorded against Google, and
the same client `ApifyWellfoundJobs` already runs against Cloudflare. A proxy
ladder (`direct` → `datacenter` → `residential`) stays in `fetcher.py` as a
fallback but is **not** exposed in the input schema: under pay-per-event the
platform bills the developer, so a visible residential toggle would let a caller
spend ~10x the bandwidth at no cost to themselves.

Rejected: BrightData Web Unlocker at ~$0.0015/request. It is ~170x more
expensive than direct egress, adds a vendor-leak surface, and there is no
maintained BrightData dataset for Clutch anyway.

## The markdown layer (the product)

Clutch publishes a native markdown rendering for AI agents: `/profile/{slug}.md`,
directory `.md`, and an `llms.txt` indexing 1,936 markdown URLs. A profile is
~250 KB of markdown against ~865 KB of HTML, and it is better structured. One
fetch yields both publish-ready markdown and a parseable source for JSON.

## Pagination quirks (each cost a debugging cycle)

- **Directory `.md` IGNORES `?page=`.** Every page returns the same companies.
  Verified by comparing name sets, not byte lengths — the lengths differ while
  the company set is identical, so a size check gives a false positive.
  Directory pagination therefore uses **HTML**, which does paginate.
- **`?page=1` 301-redirects onto page 2.** So directory page N maps to
  `?page=N+1`, and page 0 is the bare URL. The fetcher must follow redirects; a
  3xx here is canonicalization, never a block.
- **Review pagination DOES work on `.md?page=N`**, but pages 0 and 1 serve the
  same first block, so deeper paging starts at 2.
- **Sponsored and featured cards repeat across directory pages.** 151 rows over
  two pages were only 116 unique companies. `main.py` de-duplicates by profile
  URL for the whole run so a caller is never billed twice for one company.
- **Clutch renders the featured review twice**, once under a "(Featured Review)"
  heading and again in date order. `parse_reviews_markdown` collapses it, which
  is what makes parsed counts match the declared review total.
- **The `## Reviews` heading is boilerplate** and appears on 0-review profiles.
  `has_reviews` is decided by entries, never by the heading. A large share of
  profiles are thin: p50 body is only 3.6 KB.

## HTML parsing

No `lxml`, no `beautifulsoup4` — regex against stable class hooks
(`provider__title`, `provider__highlights-item`, `sg-rating__number`). Two traps:

- Anchor on the `<h3 class="provider__title">` wrapper. The `provider__title-link`
  class string also appears inside inline JavaScript.
- The "rest of opening tag" pattern must step over quoted attribute values
  (`(?:[^>"]|"[^"]*")*>`). Clutch's `data-tooltip` copy contains `>`, and a plain
  `[^>]*>` leaks tooltip text into the field value.
- Organic listings link straight to the profile; sponsored ones point at a PPC
  redirect, so the profile URL is recovered from the card body.

## Tests

`tests/run_all.sh`. Everything except the MCP-origin check runs offline against
fixtures captured 2026-08-20. `test_provider_hidden.sh` is retargeted from the
vendor-brand check (no vendor here) to a credential-leak scan.
`test_upstream_error_sanitized.py` uses an **AST** check rather than a grep: a
substring scan flags a docstring mentioning `str(exc)` and a dict literal
`{"https": proxy}` while missing real interpolation.

## Deployment

- Memory: 128 MB in `actor.json`, confirmed against three platform runs on
  2026-08-25: 51.6 MB (one profile), 81.6 MB (three directory pages), and
  81.0 MB for the worst case (10 profiles, all three output formats, 278 rows).
  The chunk loop bounds peak memory at one chunk, so a larger input does not
  raise the peak; 256 MB was doubling compute cost for nothing.
- Post-build API calls the Console does not inherit from `actor.json`:
  `PUT /v2/acts/<id>` with `defaultRunOptions` (timeoutSecs, memoryMbytes,
  build), plus title/description/seoTitle/seoDescription/categories.
- No environment variables and no secrets to configure.
