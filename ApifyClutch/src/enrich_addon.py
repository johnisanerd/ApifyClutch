"""Optional Module B business-email enrichment bolt-in for ApifyClutch.

Never raises into the host run. Opt-in via ``enrichBusinessEmail`` (default False).
Uses BounceVerify for verification and ``maxBusinessEmailsPerRun`` to cap charged
attempts (miss fees included).
"""

from __future__ import annotations

from typing import Any

_BUSINESS_EVENTS = (
    "business-email-lookup",
    "business-email-verified",
    "business-email-candidate",
)


async def maybe_enrich_business_rows(
    rows: list[dict[str, Any]],
    *,
    actor_input: dict[str, Any],
    actor: Any,
) -> list[dict[str, Any]]:
    """
    Enrich Clutch listing/profile rows with Module B fields when opted in.

    Returns ``rows`` unchanged when disabled, when the library is missing, or
    when enrichment fails (failures are logged; host delivery continues).
    """
    if not rows:
        return rows
    if not bool(actor_input.get("enrichBusinessEmail", False)):
        return rows

    try:
        from business_enrich.adapters import clutch as adapter
        from business_enrich.bolt_in import enrich_business_rows
        from business_enrich.crawl import HttpFetcher
        from enrich_core.actor_runner import ApifyActorRunner
        from enrich_core.apify_adapter import ApifyChargingAdapter
        from enrich_core.charging import (
            DryRunCharger,
            prices_from_book,
            pricing_info_from_prices,
            resolve_run_pricing,
        )
        from enrich_core.price_book import load_price_book
        from enrich_core.verifiers import BounceVerifyVerifier
    except ImportError as exc:
        actor.log.warning(
            f"enrichBusinessEmail requested but email-enrich is not installed: "
            f"{type(exc).__name__}"
        )
        return rows

    book = load_price_book()
    prices = prices_from_book(book, events=list(_BUSINESS_EVENTS))
    max_business = max(
        1, min(int(actor_input.get("maxBusinessEmailsPerRun") or 50), 500)
    )

    try:
        if actor.is_at_home():
            # Platform prices win when the event is already effective. Until
            # then (scheduled PPE, e.g. 2026-09-30) use the price-book fallback
            # so the free-plan gate does not skip the add-on.
            try:
                info = actor.get_charging_manager().get_pricing_info()
                resolved = resolve_run_pricing(info, book, list(_BUSINESS_EVENTS))
            except Exception:  # noqa: BLE001
                resolved = resolve_run_pricing(None, book, list(_BUSINESS_EVENTS))
            prices = resolved.prices
            pricing_info = resolved.pricing_info
            if resolved.using_book_fallback:
                actor.log.warning(
                    "business-email PPE events are not effective yet; "
                    "charging at price-book fallback "
                    f"(verified ${prices['business-email-verified']}, "
                    f"lookup ${prices['business-email-lookup']}). "
                    "Apify will not invoice these until they appear in pricing info."
                )
            charge: Any = ApifyChargingAdapter(
                actor, prices, platform_events=resolved.platform_events
            )
        else:
            charge = DryRunCharger(per_event_prices=prices)
            pricing_info = pricing_info_from_prices(prices)

        return await enrich_business_rows(
            rows,
            enabled=True,
            to_business_input=adapter.to_business_input,
            merge_back=adapter.merge_back,
            is_enrichable=adapter.is_enrichable_row,
            charge=charge,
            pricing_info=pricing_info,
            fetcher=HttpFetcher(),
            verifier=BounceVerifyVerifier(runner=ApifyActorRunner()),
            max_business_emails=max_business,
            found_by_actor="johnvc/clutch-agency-api",
            price_book=book,
        )
    except Exception as exc:  # noqa: BLE001 - never fail the host
        actor.log.warning(
            f"business email enrichment skipped after error: {type(exc).__name__}"
        )
        return rows
