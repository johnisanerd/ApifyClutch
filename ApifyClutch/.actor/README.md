# Clutch.co Agency API: B2B Company Data, Directory & Reviews

A company data API for Clutch.co, the B2B directory of agencies and service
providers. Walk any category directory, pull full company profiles, and
paginate every verified client review, returned as JSON **and** as
publish-ready markdown for RAG pipelines and AI agents.

Use it to build a marketing agency database, shortlist B2B service providers, or
feed verified provider data straight into a CRM or a vector store.

## What this actor returns

Three row types, chosen by `mode`:

- **`listing`**: one row per company on a directory or category page, with
  name, rating, review count, verification badges, minimum project size, hourly rate,
  team size, location, and the profile URL.
- **`profile`**: the full company record, everything above plus description,
  founding year, every office location, languages, timezones, service-line mix
  with percentages, technology focus areas, industry mix, client-size split,
  cost rating, typical project size per service, and published packages.
- **`review`**: one row per verified client review, with title, rating, the
  quality / schedule / cost / willing-to-refer breakdown, project services,
  budget band, duration, reviewer role and industry, and Clutch's own project
  and feedback summaries.

Every profile row can also carry **`markdown`**, Clutch's own LLM-ready
rendering of the page, at no extra cost. Add `html` for the raw page source.

## Use cases

- **Build a marketing agency database**: a filtered list of digital marketing
  agencies by service, location, budget band, and rating, each with its real website.
- **Competitive intelligence**: track how rivals price, which services they
  lead with, and what clients actually say about them.
- **Shortlist B2B service providers**: compare verified providers on cost
  rating, typical project size, and industry experience before an RFP.
- **CRM enrichment and AI agents**: integrate company data into a CRM using the
  API, or feed the markdown straight into a vector store without converting HTML
  to text first.
- **Market research**: measure service-mix and pricing trends across a whole
  category or country, or export a list of software development companies for
  an entire region.

## Input parameters

| Field | Type | Notes |
|---|---|---|
| `mode` | string | `directory` (default), `profiles`, or `search`. |
| `directoryUrls` | array | Any Clutch category or location page, e.g. `https://clutch.co/web-developers`. |
| `maxPagesPerDirectory` | integer | Result pages per directory URL. Each page carries 70-90 companies. |
| `profileUrls` | array | Company profiles, e.g. `https://clutch.co/profile/ignite-visibility`. A bare slug works too. |
| `searchQueries` | array | Free-text queries, e.g. `shopify development`. |
| `includeReviews` | boolean | Return every verified review as its own row. Default `true`. |
| `maxReviewsPerProfile` | integer | Cap on reviews per company. |
| `outputFormats` | array | `json`, `markdown` (default), and optionally `html`. |
| `maxItems` | integer | Hard ceiling on rows for the run. Use it as a spend cap. |

## Example output

```json
{
  "result_type": "profile",
  "name": "Ignite Visibility",
  "slug": "ignite-visibility",
  "profile_url": "https://clutch.co/profile/ignite-visibility",
  "website": "https://ignitevisibility.com",
  "rating": 4.8,
  "review_count": 175,
  "is_verified": true,
  "verification": ["Premier Verified"],
  "min_project_size": "$1,000+",
  "hourly_rate": "$100 - $149",
  "employees": "250 - 999",
  "founded_year": 2013,
  "headquarters": "San Diego, CA",
  "cost_rating": 4.7,
  "most_common_project_size": "$50,000 to $199,999",
  "service_lines": [{ "name": "Search Engine Optimization", "percent": 30 }],
  "clients": [{ "name": "Midmarket ($10M - $1B)", "percent": 50 }],
  "review_ratings": { "quality": 4.8, "schedule": 4.9, "cost": 4.7, "willing_to_refer": 4.8 },
  "markdown": "# Ignite Visibility\n## Company Information\n..."
}
```

### Output fields

Every row carries `result_type` (`listing`, `profile`, `review`, or `error`) and
`fetched_at`. Two ready-made dataset views are included: **Companies Overview**
and **Client Reviews**. Full field descriptions are in the Output tab.

## Pricing

Pay per row, with no start fee:

| Event | What it covers |
|---|---|
| `listing-scraped` | One company row from a directory page. |
| `profile-scraped` | One full company profile. |
| `review-scraped` | One verified client review. |

You are only charged for rows actually delivered. Failed pages produce an error
row and are not billed as results. `maxItems` is a hard ceiling on the run.

A company that appears on more than one directory page (sponsored and featured
cards repeat) is de-duplicated and billed **once**.

## How to get started

1. Pick a `mode`. To browse a category, leave it on `directory` and paste a
   Clutch URL such as `https://clutch.co/web-developers`.
2. Set `maxItems` as your spend ceiling.
3. Run it, then export from the dataset as JSON, CSV, or Excel.

Call it from the API:

```bash
curl -X POST "https://api.apify.com/v2/acts/johnvc~clutch-agency-api/runs?token=YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"mode":"directory","directoryUrls":["https://clutch.co/web-developers"],"maxItems":50}'
```

## 🔌 Use this API from Claude (MCP)

This Actor is MCP-compatible, so Claude and other AI agents can call it directly.
Add it to Claude Code:

```bash
claude mcp add clutch --transport http "https://mcp.apify.com/?actors=johnvc/clutch-agency-api"
```

New to Claude? [Start a free trial](https://claude.ai/referral/uIlpa7nPLg).

## 💸 Pay per run with crypto (x402)

This Actor supports x402, so an agent can pay per run in USDC without an Apify
subscription. Useful when an autonomous agent needs Clutch data on demand.

## Speed and reliability

Profiles and reviews are fetched with a browser-grade TLS fingerprint, which is
what Clutch's protection actually checks, so they come back fast and clean.
Directory category pages are heavier and better protected, so those go through
Apify's unblocking proxy for reliable results. Requests inside a chunk run in
parallel, and rows are written as each chunk completes, so a long run streams
results rather than holding them to the end.

## 🔌 Integrations

Works with everything on the Apify platform: scheduled runs and saved tasks,
n8n, Make, Zapier, webhooks, and direct REST access. Push results into Supabase,
Google Sheets, or a vector store, or let an agent call it over MCP.

## 🔗 Related tools

- [Google Maps Places API](https://apify.com/johnvc/google-maps-places-api?fpr=9n7kx3): local business data with contact details.
- [LinkedIn Company API](https://apify.com/johnvc/linkedin-company-api?fpr=9n7kx3): company firmographics and headcount.
- [Crunchbase Company API](https://apify.com/johnvc/crunchbase-company-api?fpr=9n7kx3): funding and investor data.
- [G2 Reviews API](https://apify.com/johnvc/g2-reviews-api?fpr=9n7kx3): software reviews, the B2B software counterpart to Clutch.

More at [Alpha OSINT](https://www.alphaosint.com).

## ❓ FAQ

**Do I need a Clutch account or API key?**
No. There is nothing to configure. Just give it a URL.

**Can I get every review for a company?**
Yes. Set `includeReviews` to true and raise `maxReviewsPerProfile`. Reviews are
paginated until the company's declared total is reached.

**What is the markdown for?**
It is Clutch's own AI-oriented rendering of the page. Because it comes from the
same request as the structured data, it costs nothing extra, and it is a better
RAG input than HTML converted to text.

**Why does a directory page return more than 50 companies?**
Clutch mixes sponsored and featured cards into the organic list. All of them are
returned, de-duplicated across pages so you are never billed twice.

**Do I get the company's real website?**
Yes. Clutch's outbound links carry referral tracking; those parameters are
stripped so you get the clean homepage URL.

**Why do some profiles have so few fields?**
Many Clutch profiles are genuinely thin. A large share have no reviews and
little published detail. Those rows return what exists rather than guessing.

**Can I filter by rating or location?**
Use a Clutch directory URL that already encodes the filter (Clutch has pages for
most service and location combinations), then filter the dataset afterwards.

**How do I integrate this company data into a CRM using an API?**
Run the Actor over the API or a webhook, then map `name`, `website`, `location`,
`employees`, and `min_project_size` onto your CRM fields. Every row is flat JSON,
so most CRMs will take it without a transformation layer in between.

**How does a company data enrichment API work here?**
You give it a profile URL, or let directory mode find the companies for you.
Each row comes back with firmographics, service mix, pricing bands, and review
history already parsed into fields, so there is nothing left to extract.

**Which B2B data platform is better for lead generation?**
It depends on what you are qualifying on. General firmographic providers know
company size and industry. Clutch knows what a buyer weighs when picking a
vendor: verified client reviews, published rates, minimum project size, and
service mix. This Actor gives you that second layer.

**How do B2B lead generation tools compare to agencies?**
A tool gives you the raw list and the filters; an agency gives you outreach. This
Actor covers the first half, and the data it returns is what most agencies would
be sourcing manually anyway.

**Is scraping Clutch.co legal?**
This Actor collects only publicly available pages. You are responsible for how
you use the data, including under GDPR and CCPA where applicable.

## 🌐 About Alpha OSINT

Built and maintained by [Alpha OSINT](https://www.alphaosint.com), a portfolio
of open-source-intelligence and market-data APIs on Apify.
