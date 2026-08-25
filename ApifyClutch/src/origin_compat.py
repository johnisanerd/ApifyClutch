"""Compatibility shim: tolerate run origins the Apify SDK does not know about
(e.g. "MCP" from the hosted Apify MCP server). Apply at import time, before
Actor.init() runs. Idempotent. Fully guarded: any failure logs a warning and
returns without raising, so the shim cannot itself crash the actor.

Two crash surfaces in the wild:
  - SDK 2.7.x: charging_manager.__aenter__ -> run_validator.validate_python(),
    which checks meta.origin against apify_shared.consts.MetaOrigin (an enum).
  - SDK 3.x:   Actor.set_status_message() -> ActorRun.model_validate(), which
    checks meta.origin against apify_client._literals.RunOrigin (a Literal).

This shim patches both paths defensively. Required by Rule #30.
"""
from __future__ import annotations
import logging

logger = logging.getLogger("apify.origin_compat")
_SENTINEL = "_origin_compat_patched"
_FALLBACK_ORIGIN = "API"
_KNOWN_NEW_ORIGINS = ("MCP",)
_KNOWN_ALLOW = {
    "DEVELOPMENT", "WEB", "API", "SCHEDULER", "TEST",
    "WEBHOOK", "ACTOR", "STANDBY", "CLI",
}


def _patch_2x_meta_origin() -> bool:
    """Register MCP as a real MetaOrigin enum member and add a _missing_ catch-all.
    Rebuild Pydantic schemas and the charging-manager validator so every 2.7.x
    validation path accepts unknown origins. Returns True if patched or already
    patched, False if the 2.7.x surface is not present.
    """
    try:
        from apify_shared.consts import MetaOrigin
    except Exception:
        return False
    if getattr(MetaOrigin, _SENTINEL, False):
        return True
    try:
        for name in _KNOWN_NEW_ORIGINS:
            if name not in MetaOrigin._value2member_map_:
                member = str.__new__(MetaOrigin, name)
                member._name_ = name
                member._value_ = name
                MetaOrigin._member_map_[name] = member
                MetaOrigin._value2member_map_[name] = member
                MetaOrigin._member_names_.append(name)
        fallback = MetaOrigin._value2member_map_.get(_FALLBACK_ORIGIN)
        if fallback is not None:
            def _missing(cls, value, _fb=fallback):
                logger.info("origin_compat: unknown run origin %r -> %r", value, _fb.value)
                return _fb
            MetaOrigin._missing_ = classmethod(_missing)
        try:
            import apify._models as models
            for cls_name in ("ActorRunMeta", "ActorRun"):
                cls = getattr(models, cls_name, None)
                if cls is not None and hasattr(cls, "model_rebuild"):
                    cls.model_rebuild(force=True)
        except Exception:
            pass
        try:
            import apify._charging as charging
            if hasattr(charging, "run_validator"):
                from typing import Union
                from pydantic import TypeAdapter
                import apify._models as models
                charging.run_validator = TypeAdapter(Union[models.ActorRun, None])
        except Exception:
            pass
        setattr(MetaOrigin, _SENTINEL, True)
        return True
    except Exception as exc:
        logger.warning("origin_compat: 2.x patch failed: %s", exc)
        return False


def _patch_3x_run_origin() -> bool:
    """Wrap ActorRun.model_validate so a ValidationError on meta.origin remaps
    the value to the fallback origin and retries. Patches both apify_client and
    apify SDK ActorRun models where they exist. Returns True if at least one
    surface was patched (or already patched), False otherwise.
    """
    candidates = []
    try:
        from apify_client._models import ActorRun as ClientActorRun
        candidates.append(ClientActorRun)
    except Exception:
        pass
    try:
        from apify._models import ActorRun as SdkActorRun
        candidates.append(SdkActorRun)
    except Exception:
        pass
    if not candidates:
        return False
    try:
        from pydantic import ValidationError
    except Exception:
        return False

    patched_any = False
    for cls in candidates:
        if getattr(cls, _SENTINEL, False):
            patched_any = True
            continue
        try:
            original_validate = cls.model_validate

            def _sanitize(obj):
                if isinstance(obj, dict) and isinstance(obj.get("meta"), dict):
                    meta = obj["meta"]
                    if meta.get("origin") not in _KNOWN_ALLOW:
                        new_meta = dict(meta)
                        logger.info(
                            "origin_compat: unknown run origin %r -> %r",
                            new_meta.get("origin"), _FALLBACK_ORIGIN,
                        )
                        new_meta["origin"] = _FALLBACK_ORIGIN
                        new_obj = dict(obj)
                        new_obj["meta"] = new_meta
                        return new_obj
                return obj

            def safe_model_validate(obj, *args, _orig=original_validate, **kwargs):
                try:
                    return _orig(obj, *args, **kwargs)
                except ValidationError as err:
                    if any(
                        "meta" in str(e.get("loc", ())) and "origin" in str(e.get("loc", ()))
                        for e in err.errors()
                    ):
                        return _orig(_sanitize(obj), *args, **kwargs)
                    raise

            cls.model_validate = classmethod(lambda c, obj, *a, **k: safe_model_validate(obj, *a, **k))
            setattr(cls, _SENTINEL, True)
            patched_any = True
        except Exception as exc:
            logger.warning("origin_compat: 3.x patch on %s failed: %s", cls, exc)
            continue
    return patched_any


def patch_unknown_run_origins() -> None:
    """Apply both 2.7.x and 3.x compatibility patches. Safe to call multiple times."""
    try:
        ok_2x = _patch_2x_meta_origin()
        ok_3x = _patch_3x_run_origin()
        if ok_2x or ok_3x:
            logger.info("origin_compat: patches applied (2.x=%s, 3.x=%s)", ok_2x, ok_3x)
        else:
            logger.info("origin_compat: no patches applied (neither SDK surface present)")
    except Exception as exc:
        logger.warning("origin_compat: top-level failure: %s", exc)
