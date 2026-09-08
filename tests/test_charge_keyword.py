"""Regression test: Actor.charge must receive count as a keyword.

apify 4 changed the signature to charge(event_name, *, count=1). The old
positional call raised TypeError inside _charge's except-all, which logged a
warning, returned False, and delivered every row for free. The fake Actor here
mirrors the real keyword-only signature, so the positional form fails offline
instead of on a paid run. It also locks the guard branch: _charge delegates to
FreeTierGuard.charge (positional-or-keyword count) and never hits Actor.charge.

Exits non-zero on any failed check so run_all.sh can gate publish on it.

Layout note: tests/ is at the repo root; the actor (with src/) is in ApifyClutch/.
"""
from __future__ import annotations

import asyncio
import inspect
import sys
import types
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
ACTOR_DIR = THIS_DIR.parent / "ApifyClutch"   # the actor subfolder that contains src/
sys.path.insert(0, str(ACTOR_DIR))

failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"PASS: {name}")
    else:
        print(f"FAIL: {name}" + (f" - {detail}" if detail else ""))
        failures.append(name)


# 1. The installed SDK's contract. If this ever flips back, the checks below
#    still hold; the keyword form works on both signatures.
import apify  # noqa: E402

_kind = inspect.signature(apify.Actor.charge).parameters["count"].kind
check("installed apify: Actor.charge count is keyword-only",
      _kind is inspect.Parameter.KEYWORD_ONLY, f"kind={_kind}, apify {apify.__version__}")

from src import main as M  # noqa: E402

calls: list[tuple[str, int]] = []
warns: list[tuple] = []
limit = {"reached": False}


async def fake_charge(event_name: str, *, count: int = 1):
    calls.append((event_name, count))
    return types.SimpleNamespace(event_charge_limit_reached=limit["reached"])


fake_actor = types.SimpleNamespace(
    is_at_home=lambda: True,
    charge=fake_charge,
    log=types.SimpleNamespace(info=lambda *a, **k: None,
                              warning=lambda *a, **k: warns.append(a)),
)

real_actor, real_guard = M.Actor, M._guard
M.Actor = fake_actor
M._guard = None  # the fallback branch: no free-tier guard running
try:
    # 2. Fallback branch charges through Actor.charge with count as a keyword.
    got = asyncio.run(M._charge("profile-scraped", 3))
    check("fallback: below limit returns False", got is False, f"got {got!r}")
    check("fallback: count passed as keyword", calls == [("profile-scraped", 3)], f"calls={calls!r}")
    check("fallback: no failure warning", warns == [], f"warns={warns!r}")
    limit["reached"] = True
    got = asyncio.run(M._charge("profile-scraped", 1))
    check("fallback: limit reached returns True", got is True, f"got {got!r}")

    # 3. Guard branch delegates to the guard (positional-or-keyword count) and
    #    never touches Actor.charge. Protects the `_guard.charge(event_name, count)`
    #    line from an over-eager keyword rewrite.
    guard_calls: list[tuple[str, int]] = []

    class FakeGuard:
        async def charge(self, event_name: str, count: int = 1) -> bool:
            guard_calls.append((event_name, count))
            return True

    calls.clear()
    M._guard = FakeGuard()
    got = asyncio.run(M._charge("review-scraped", 2))
    check("guard: delegates to guard.charge", guard_calls == [("review-scraped", 2)],
          f"guard_calls={guard_calls!r}")
    check("guard: returns the guard's verdict", got is True, f"got {got!r}")
    check("guard: Actor.charge not called", calls == [], f"calls={calls!r}")

    # 4. Local runs never charge.
    calls.clear()
    M._guard = None
    fake_actor.is_at_home = lambda: False
    got = asyncio.run(M._charge("profile-scraped", 1))
    check("local: returns False without charging", got is False and calls == [],
          f"got={got!r} calls={calls!r}")
finally:
    M.Actor, M._guard = real_actor, real_guard

if failures:
    print(f"\n{len(failures)} failure(s): {failures}", file=sys.stderr)
    sys.exit(1)
print("\nAll Actor.charge keyword regression checks passed.")
sys.exit(0)
