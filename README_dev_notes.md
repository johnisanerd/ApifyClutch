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
the same client `ApifyWellfoundJobs` already runs against Cloudflare.

### CORRECTION 2026-09-07: that 0-challenge run was from a residential IP

The 150-fetch measurement above was taken from a residential Mac IP, **not the
platform**. On Apify's shared **datacenter egress** (where the actor actually
runs), the light `.md` profile/search pages stay reliable, but the **heavy
directory pages (1.2 to 2.7 MB HTML/md) get a Cloudflare soft challenge**: a
`200` with a partial or empty body and no `cf-mitigated` header, which direct
challenge detection cannot see. Directory task runs returned 18 / 40 / 45 / 50 /
0 rows for categories that return 50 to 90 locally, and one run exited silently
with 0 rows (fixed: the chunk loop now uses `return_exceptions=True` and turns a
raised fetch into an error row).

**Fix: split routing.** Directory fetches go through **Apify Unblocker**
(`create_proxy_configuration(groups=['UNBLOCKER'])`, `start_tier="unblocker"`);
profiles/search stay direct and escalate to Unblocker only on a real challenge.

### Why Unblocker, and why not residential (the economics, for context)

- Unblocker **bills per successful request** (~$0.0025/request on Free/Starter;
  failed requests are not charged), **not per GB**. Directory pages are heavy, so
  per-request pricing is decisive: $0.0025/page over 50 to 90 rows =
  **$0.00003 to $0.00005/row COGS** vs a listing net of $0.00024/row (developer
  keeps 80% of the $0.0003 BRONZE price) = **~5 to 8x margin**.
- Residential ($8/GB) on a 1.2 to 2.7 MB page = $0.01 to $0.022/page = $0.0002 to
  $0.00043/row, which **meets or exceeds** the listing price. Rejected.
- Apify first-party, so **no third-party vendor, no Rule #6 leak surface, no new
  dependency**. The `datacenter` and `residential` tiers were removed from
  `build_fetcher`; the ladder is now `direct` -> `unblocker`.

The split-routing pattern (heavy/challenged pages -> Unblocker; light pages ->
direct) likely applies to other datacenter-challenged actors whose transport was
validated off-platform. Confirm the per-request rate in the Console usage
dashboard; the docs quote "$2.5 / 1,000 SERPs" for the same proxy line.

Rejected earlier: BrightData Web Unlocker at ~$0.0015/request. It adds a
vendor-leak surface, and there is no maintained BrightData dataset for Clutch.
Apify Unblocker is the first-party equivalent with none of that.

### CORRECTION 2026-09-08: "profiles/search intermittently return 0" was a measurement artifact

After the Unblocker + v4 + free-tier deploy (build 1.0.19), repeated task sweeps
appeared to show profiles/search modes returning 0 rows at random (directory
looked reliable, profiles/search did not). **This was false.** Root cause: the
sweep read the dataset's `itemCount` **metadata** field immediately after the run
finished, and that field is **eventually-consistent** — it lags at 0 for several
seconds before catching up. Reading the actual `/datasets/{id}/items` array
instead showed every run had its full row set the whole time.

Proof (build 1.0.19, `rag-prof` task, 4 back-to-back runs): each run fetched both
profiles with **full bodies** (`ok=True status=200 bytes=253141` / `172387`) and
had **2 real items**, while `itemCount` metadata read `0, 2, 0, 2`. A clean
3x sweep of all 7 tasks reading the items array: `mktr-dir 50/50/50`,
`dev-prof 3/3/3`, `rag-prof 2/2/2`, `monitor 21/21/21`, `search 4/4/4`,
`zh-dir 50/50/50`, `zh-prof 21/21/21`. **The actor is reliable in every mode.**

Why directory *looked* fine while profiles/search *looked* broken: directory mode
logs `rows=N` via `Actor.log.info` (a real log line, read correctly), so its
counts were never taken from the lagging metadata. Profiles/search had no
equivalent summary log line, so the checker fell back to `itemCount`. **Lesson:
to judge run output, read the items array (or the run log), never the
eventually-consistent `itemCount` right after finish.** The `directory` best-of-N
retry is still correct and useful (Clutch really does serve partial heavy pages),
but no best-of-N was ever needed on the light profile/search `.md` pages — they
come back complete on the first Unblocker fetch.

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

## Pricing (first-time monetization, 2026-09-03)

No upstream vendor, so break-even is Apify compute + egress only. Measured from
`usageTotalUsd` on real runs: the worst case (10 profiles, all formats, 278 rows)
cost **$0.00219 total = ~$0.000008/row**. On pay-per-event Apify covers
paying-user compute, so effective developer COGS is ~$0. Every tier clears
break-even by 30-300x; the pricing decision is positioning, not cost.

Strategy: **Loss Leader on listings, Market-undercut on profiles.** Cheapest in
category on all three events while FREE tier holds at/above the nearest rival.

| Event (primary = profile) | FREE | BRONZE | SILVER | GOLD | vs market |
|---|---|---|---|---|---|
| profile-scraped | 0.0029 | 0.0025 | 0.00225 | 0.0020 | GOLD 31% under memo23 ($0.0029) |
| listing-scraped | 0.0004 | 0.0003 | 0.00027 | 0.00024 | GOLD 6x under piotrv1001 ($0.0015) |
| review-scraped | 0.0004 | 0.0003 | 0.00027 | 0.00024 | GOLD under everyone |
| apify-default-dataset-item | | | | 0.00001 | platform accounting, flat |

PLATINUM and DIAMOND inherit GOLD. No `apify-actor-start` event: the README
promises "no start fee," and the tripadvisor precedent runs with only the
dataset-item default. First-time monetization is immediate (no 14-day window).

Benchmarks (live 2026-09-03): memo23 (leader, 740 users, 4.6 stars, company
$0.0029 + $0.00075/review), crawlerbros (broken incumbent, 1345 users, 16%
success, GOLD $0.005 + $0.005 start), piotrv1001 (listings $0.0015, 1218 users),
fatihtahta (price floor, listing $0.0007-0.0009). A memo23 "company + 10 reviews"
is $0.0104; ours is $0.0055 for the same data plus markdown.

`.actor/actor.json` mirrors the BRONZE tier and is in sync. Ledger:
`ApifyUpdate/pricing_changes.json` + `PRICING_LEDGER.md`.
