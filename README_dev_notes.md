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

## Launch status and distribution chain (2026-09-08)

Fast reference for the whole launch. IDs and URLs first, then what shipped, then
the operational gotchas that cost a cycle each.

### Identity

| Thing | Value |
|---|---|
| Actor slug | `johnvc/clutch-agency-api` |
| Actor id | `JYnIiqxn4hMnWZiKQ` |
| Source repo | `github.com/johnisanerd/ApifyClutch` (double-nested: build folder is `ApifyClutch/ApifyClutch`) |
| Git deploy | git-linked auto-publish. **Deploy only via `git push`, never `apify push`** (Rule #3; early 1.0.1-1.0.6 builds broke this before the repo was linked). Always verify `HEAD == origin/main` after a push. |
| Example repo | `github.com/johnisanerd/Apify-Clutch-Agency-API` (public) |
| AlphaOSINT page | `website-alphaosint/_sources/clutch-agency-api.md` (live) |
| Commit convention | authored `John Cole <29712567+johnisanerd@users.noreply.github.com>`, **no Claude attribution / Co-Authored-By trailer** (matches the repo history; overrides the global default) |

### Build history

- **1.0.19** — Unblocker split-routing + SDK v4 (`apify==4.0.2`, `apify-client==3.2.0`, `apify-shared` dropped) + free-tier cap + profiles/search diagnostics.
- **1.0.20** — added the terminal `Actor.log.info("Run complete: N row(s) collected.")` line (see below) and the Featured tasks README section.
- **1.0.21** — doc-only: the Agent skills backlink section in the actor README. `README_dev_notes.md` and `tasks/` live at the repo ROOT, outside the `ApifyClutch/ApifyClutch` build folder, so editing them does not change the actor image.

SDK v4 was a **pin-only** migration: no `src/*.py` changes, `origin_compat.py` still required (the released `apify-shared` still lacks MCP in `MetaOrigin`, so the Rule #30 shim does real work under v4). Default run options that the Console does not inherit from `actor.json`: build=latest, memory 128 MB, timeout 600 s. Re-apply them if a push ever resets them.

### Free-tier cap

Shared per-actor monthly cap for free users (`FreeTierGuard`, Supabase-backed).
Wired in `src/main.py`: import, `FreeTierGuard.start()` after input validation
and before any fetch, `_guard.charge` on every charge site, `close()` in
`finally`. `FREE_MAX=$1.00`, **not marked secret** (a secret var redacts to
`$*********`, which the guard cannot parse, so it would silently read 0 and go
inert). Cap installed **2026-09-04**; `prune_stale_builds.py --actor
JYnIiqxn4hMnWZiKQ` reports **0 stale builds** (every surviving build is post-cap),
so the pre-cap-bypass gap is closed. A paid run logs "Paid Apify account detected"
and the guard no-ops.

### Charging (verified on-platform)

`chargedEventCounts` maps correctly per mode: directory run -> `listing-scraped`
N; profiles run -> `profile-scraped` N; monitor run -> `profile-scraped` 1 +
`review-scraped` M. Run-level dedup by profile URL holds (a repeated company is
billed once). Measured all-in cost: a 50-row directory run ~$0.0007 total
(~$0.000014/row) via Unblocker, ~13x under the listing net price. Profiles/reviews
COGS trivial.

### Task pages (7 published 2026-09-08)

Published by REST `PUT /v2/actor-tasks/{id}` with `isPublic:true` + `publicConfig`
(`seoTitle` <=60, `seoDescription` <=160, `inputSchemaFields`, `datasetView`).
**Publish requires a task-level `title` AND `description`** or it 400s with
"Cannot publish Actor task: Description is required" (house pattern: set
`title`=`seoTitle`, `description`=`seoDescription`). Verify with the
`/examples/{slug}.md` page returning 200 (NOT `/{slug}` and NOT `/{slug}.md`,
which both just serve the actor SPA shell). Registered in `ApifyUpdate/tasks.json`
+ Featured tasks README (Rule #25) + `tasks/TASK-*.md` sheets.

| Task id | Slug | Mode | Bucket |
|---|---|---|---|
| `64gy310cSD6m4kUzG` | export-a-list-of-digital-marketing-agencies-from-clutch | directory | Marketer |
| `AOvoKnTtrTXs2U2YV` | get-clutch-company-data-as-json-for-your-crm | profiles | Developer |
| `JT1pnISksF9PBWCkL` | clutch-agency-data-as-llm-ready-markdown-for-rag | profiles | AI/RAG |
| `YyE61CsaLhf26oNV1` | monitor-a-clutch-companys-client-reviews | profiles+reviews | Monitor |
| `jnQPaxhJwGWC5o6NW` | find-shopify-development-agencies-on-clutch | search | Search |
| `sBBT5RtyU6bPQRRnj` | clutch-marketing-agency-list-cn | directory | zh-Hans (Rule #18) |
| `aVTHziIj11sSgki5l` | clutch-company-profiles-reviews-cn | profiles+reviews | zh-Hans (Rule #18) |

5 distinct English intent buckets + 2 Simplified-Chinese pages clears the Rule #26
diversity floor.

### Agent skills (2 published 2026-09-08)

Keyword pair from `KEYWORDS-clutch-agency-api-2026-08-25.md`: `company data api`
(WINNABLE, best; profiles mode) and `marketing agency database` (WINNABLE;
directory mode). Built with the `apify-publish-agent-skills` workflow.

- Canonical: `ApifyUpdate/agent-skills/clutch-agency-api/apify-{company-data-api,marketing-agency-database}` (`fp_sid=skillrepo`).
- Public repos (P1-P3, `npx skills add` telemetry run): `johnisanerd/claude-skill-company-data-api`, `johnisanerd/claude-skill-marketing-agency-database`.
- awesome-skills PRs (P4, one skill per PR): `apify/awesome-skills#106` (company-data-api), `#107` (marketing-agency-database), `fp_sid=awesomeskills`, awaiting maintainer merge.
- MCP registry (P5, published + confirmed live): `io.github.johnisanerd/clutch-agency-api` v1.0.0, hosted `mcp.apify.com` remote, via `mcp-publisher` (`server.json` under `agent-skills/clutch-agency-api/syndication/tier4-mcp-registry/`). Glama/mcp.so/PulseMCP inherit from the registry.
- Ledger: `ApifyUpdate/skills_published.json`. Actor README carries an Agent skills backlink to both repos.

### MCP discovery (Rule #14) and competitive landscape

Does **not** rank top-5 for "clutch" or "clutch agency" (new actor, ~0 usage;
rank ~= title-keyword x popularity). The niche is now **saturated: 13+ rivals**.
Only `danthedataman/clutch-agency-directory` has real traction (~12 monthly);
memo23 (818 users, quality leader, $0.0029/company) and crawlerbros (1347 users,
broken incumbent) hold the installed base; the rest (`dami_studio`, `psymall`,
`khadinakbar`, `happitap`, `saswave`, `powerai`, `automation-lab`, `samstorm`,
`jungle_synthesizer`) are mostly pre-flywheel. We are the price floor on
directory/listing rows and the only one with multi-format markdown + a real
reviews-pagination mode. Win path = usage flywheel from the task pages.

### Operational gotchas learned this launch (each cost a cycle)

1. **Dataset `itemCount` metadata is eventually-consistent.** It reads 0 for
   seconds after a run finishes. Judging output by it produced a phantom
   "profiles/search return 0 rows" bug that was never real (see the CORRECTION
   2026-09-08 section above). Read the `/datasets/{id}/items` array or a run-log
   summary line instead. This is why the terminal `Run complete: N row(s)` log
   line was added: `set_status_message` sets the run status message, which is NOT
   in the log stream, so there was no log-readable ground truth for the light
   modes.
2. **Task publish needs `title` + `description`,** not just `publicConfig`
   (400 "Description is required"). Verify via `/examples/{slug}.md` = 200.
3. **awesome-skills `generate_agents.py` requires `metadata.keywords`** in the
   SKILL.md frontmatter (comma-separated string). The bundled `validate.sh` does
   NOT check it, so a skill passes local validation then fails the PR gate. Put
   `metadata.keywords` in the canonical SKILL.md from the start so all copies match.
4. **MCP `server.json`:** `description` must be <=100 chars (a longer one 422s),
   use the current `2025-12-11` schema, `remotes` for the hosted mcp.apify.com
   server (no npm publish). `mcp-publisher login github` is a device flow; once
   authed, `token.json` in `~/.config/mcp-publisher` lets `publish` run.
5. **awesome-skills fork name:** the single `johnisanerd` fork of
   `apify/awesome-skills` was renamed (`awesome-skills-zillow-price-cuts` from a
   prior launch); the local clone's `origin` redirects to it. Branch per skill
   from `upstream/main`, stage only the 4 PR files (SKILL.md + 2 references + the
   one `marketplace.json` entry); revert the regenerated `README.md` and
   `agents/AGENTS.md` before committing.

### Remaining launch chain

Shipped: actor, pricing, README SEO, 7 tasks, example repo, AlphaOSINT page,
cross-link tokens, monthly keep-alive schedules, 2 agent skills (repos + MCP
registry + PRs open). **Still to do:** n8n node, long-form articles, low-priority
P6 skill-directory submissions. Hide-source confirmed checked by John (Rule #31).
