# Task publish sheet: Clutch 公司资料与客户评价 API (JSON)

Paste-ready values for the Apify Console Publication tab. Published via REST API 2026-09-08.

## Parent Actor

- **Actor name:** Clutch.co Agency API
- **Store link:** https://apify.com/johnvc/clutch-agency-api?fpr=9n7kx3
- **Repo:** /Users/johncole/Github/ApifyClutch
- **Username / slug:** johnvc / clutch-agency-api

## Target keyword

`clutch 公司资料 客户评价`

## Display information (Publication tab)

- **Slug:** `clutch-company-profiles-reviews-cn`
- **SEO task title:** `Clutch 公司资料与客户评价 API (JSON)`
- **SEO description:** `按公司主页批量采集 Clutch 完整资料与已验证客户评价，输出 JSON：评分、服务构成、报价区间、项目规模、评价明细与评价人角色。适合供应商尽调与 CRM 数据补全。`

Final public URL: `https://apify.com/johnvc/clutch-agency-api/examples/clutch-company-profiles-reviews-cn?fpr=9n7kx3`

## Input (visible fields)

| Field (property name) | Value | Visible? |
|-----------------------|-------|----------|
| `mode` | "profiles" | yes |
| `profileUrls` | ["ignite-visibility"] | yes |
| `includeReviews` | true | yes |
| `maxReviewsPerProfile` | 20 | yes |
| `maxItems` | 25 | yes |

No credential-shaped input fields. This Actor needs no API key: transport is curl_cffi (direct) with Apify Unblocker for heavy directory pages, both first-party.

## Dataset schema

- **View chosen:** `reviews`

## Publish status

- **Actor ID:** JYnIiqxn4hMnWZiKQ
- **Task ID:** aVTHziIj11sSgki5l
- **Published:** 2026-09-08 (isPublic true, verified via /examples/clutch-company-profiles-reviews-cn.md = HTTP 200)
- **Landing page:** https://apify.com/johnvc/clutch-agency-api/examples/clutch-company-profiles-reviews-cn?fpr=9n7kx3
- **AI-readable (.md):** https://apify.com/johnvc/clutch-agency-api/examples/clutch-company-profiles-reviews-cn.md

## Internal notes (NOT public)

- No third-party data vendor. Transport = curl_cffi direct + Apify Unblocker (Apify first-party). Nothing to hide in public copy.
- Reliability verified 3x per task 2026-09-08: this task returned a stable non-zero row set every run.
- Rule #18 Simplified-Chinese (zh-Hans) task page.
Last Updated: 2026.09.08
