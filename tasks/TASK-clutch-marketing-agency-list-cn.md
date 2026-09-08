# Task publish sheet: 从 Clutch 导出数字营销机构列表 (JSON)

Paste-ready values for the Apify Console Publication tab. Published via REST API 2026-09-08.

## Parent Actor

- **Actor name:** Clutch.co Agency API
- **Store link:** https://apify.com/johnvc/clutch-agency-api?fpr=9n7kx3
- **Repo:** /Users/johncole/Github/ApifyClutch
- **Username / slug:** johnvc / clutch-agency-api

## Target keyword

`数字营销机构列表`

## Display information (Publication tab)

- **Slug:** `clutch-marketing-agency-list-cn`
- **SEO task title:** `从 Clutch 导出数字营销机构列表 (JSON)`
- **SEO description:** `从 Clutch 目录批量导出数字营销机构，输出 JSON：机构名称、评分、评价数、最低预算、每小时费率、团队规模、所在地和官网。适合销售线索开发与市场调研。`

Final public URL: `https://apify.com/johnvc/clutch-agency-api/examples/clutch-marketing-agency-list-cn?fpr=9n7kx3`

## Input (visible fields)

| Field (property name) | Value | Visible? |
|-----------------------|-------|----------|
| `mode` | "directory" | yes |
| `directoryUrls` | ["https://clutch.co/agencies/digital-marketing"] | yes |
| `maxPagesPerDirectory` | 1 | yes |
| `maxItems` | 50 | yes |

No credential-shaped input fields. This Actor needs no API key: transport is curl_cffi (direct) with Apify Unblocker for heavy directory pages, both first-party.

## Dataset schema

- **View chosen:** `companies`

## Publish status

- **Actor ID:** JYnIiqxn4hMnWZiKQ
- **Task ID:** sBBT5RtyU6bPQRRnj
- **Published:** 2026-09-08 (isPublic true, verified via /examples/clutch-marketing-agency-list-cn.md = HTTP 200)
- **Landing page:** https://apify.com/johnvc/clutch-agency-api/examples/clutch-marketing-agency-list-cn?fpr=9n7kx3
- **AI-readable (.md):** https://apify.com/johnvc/clutch-agency-api/examples/clutch-marketing-agency-list-cn.md

## Internal notes (NOT public)

- No third-party data vendor. Transport = curl_cffi direct + Apify Unblocker (Apify first-party). Nothing to hide in public copy.
- Reliability verified 3x per task 2026-09-08: this task returned a stable non-zero row set every run.
- Rule #18 Simplified-Chinese (zh-Hans) task page.
Last Updated: 2026.09.08
