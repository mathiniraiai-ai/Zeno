"""Dedicated FastAPI v2 video & fleet tracker router (`service/tracker_api.py`).

Self-contained inside `service/` (imports only stdlib, FastAPI, Pydantic).
Handles:
  - POST /v2/track/events      (gzipped or plain JSON batch event ingestion with per-event idempotency)
  - POST /v2/track/heartbeat   (+ POST /v1/jobs/progress backward-compatible alias)
  - GET  /v2/track/summary     (dashboard summary: 1 row = 1 video, immutable completion day, stuck detection)
  - GET  /v2/track/jobs/{key}  (full timeline, prompts, revisions, errors for one video job)
  - GET  /v2/track/errors      (5-category error & stuck radar)
  - GET  /v2/track/fleet       (per-editor / per-machine verified OTA update status)
"""
from __future__ import annotations

import gzip
import json
import logging
import re
import time
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Header, HTTPException, Request

log = logging.getLogger("zeno.service.tracker")
NO_STORE = {"Cache-Control": "no-store"}
MAX_RAW_BODY_BYTES = 2 * 1024 * 1024
MAX_DECOMPRESSED_BYTES = 8 * 1024 * 1024


def _safe_header(request: Request, name: str, max_len: int = 80) -> str:
    val = str(request.headers.get(name) or "").strip()
    return re.sub(r"[^0-9A-Za-z .+_:/@()-]", "", val)[:max_len]


def _resolve_email(ks: Any, key_id: Optional[int], fallback_name: str, body_email: str = "") -> str:
    if key_id is not None and hasattr(ks, "get_key_email"):
        try:
            em = str(ks.get_key_email(key_id) or "").strip().lower()
            if em:
                return em
        except Exception:
            pass
    if body_email and "@" in body_email:
        return body_email.strip().lower()[:120]
    return fallback_name.strip().lower()[:120]


async def _read_json_or_gzip(request: Request) -> Any:
    raw = await request.body()
    if len(raw) > MAX_RAW_BODY_BYTES:
        raise HTTPException(413, "Tracker payload too large")
    enc = str(request.headers.get("content-encoding") or "").strip().lower()
    if enc == "gzip" or raw[:2] == b"\x1f\x8b":
        try:
            raw = gzip.decompress(raw)
        except Exception:
            raise HTTPException(400, "Invalid gzip payload")
        if len(raw) > MAX_DECOMPRESSED_BYTES:
            raise HTTPException(413, "Decompressed tracker payload too large")
    if not raw.strip():
        raise HTTPException(400, "Empty JSON body")
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        raise HTTPException(400, "Invalid JSON payload")


def build_tracker_router(
    *,
    store_fn: Callable[[], Any],
    authenticate_fn: Callable[[Optional[str]], Any],
    check_rate_fn: Callable[[str], None],
    check_viewer_fn: Callable[[Optional[str], Optional[str]], Dict[str, str]],
    client_version_fn: Callable[[Optional[Request]], Any],
    note_version_fn: Callable[[Optional[int], str, str, str, str], None],
) -> APIRouter:
    router = APIRouter()

    @router.post("/v2/track/events")
    async def track_events_batch(
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        name, key_id = authenticate_fn(authorization)
        check_rate_fn(name)
        body = await _read_json_or_gzip(request)
        if isinstance(body, dict) and isinstance(body.get("events"), list):
            events: List[Any] = body["events"]
        elif isinstance(body, list):
            events = body
        elif isinstance(body, dict):
            events = [body]
        else:
            raise HTTPException(400, "Expected JSON object with events list")
        if len(events) > 200:
            raise HTTPException(400, "Too many events in one batch (max 200)")

        hdr_ver, hdr_plat = client_version_fn(request)
        hdr_mid = _safe_header(request, "x-zeno-machine-id", 80)
        ks = store_fn()
        email = _resolve_email(ks, key_id, name, str(body.get("editor_email") or "") if isinstance(body, dict) else "")
        if hdr_ver and key_id is not None:
            note_version_fn(key_id, name, hdr_ver, hdr_plat, "tracker")

        results: List[Dict[str, Any]] = []
        accepted = 0
        duplicates = 0
        rejected = 0
        for ev in events:
            if not isinstance(ev, dict):
                rejected += 1
                results.append({"event_id": "", "status": "rejected", "reason": "event must be an object"})
                continue
            enriched = dict(ev)
            if hdr_ver and not enriched.get("zeno_version"):
                enriched["zeno_version"] = hdr_ver
            if hdr_plat and not (enriched.get("platform") or enriched.get("client_platform")):
                enriched["platform"] = hdr_plat
            if hdr_mid and not enriched.get("machine_id"):
                enriched["machine_id"] = hdr_mid
            try:
                res = ks.ingest_track_event(key_id, name, email, enriched)
            except Exception as exc:
                log.exception("tracker ingest failed for event_type=%s", enriched.get("event_type"))
                res = {"event_id": str(enriched.get("event_id") or ""), "status": "rejected", "reason": type(exc).__name__}
            st = res.get("status")
            if st == "accepted":
                accepted += 1
            elif st == "duplicate":
                duplicates += 1
            else:
                rejected += 1
            results.append(res)

        return {
            "ok": True,
            "accepted": accepted,
            "duplicates": duplicates,
            "rejected": rejected,
            "results": results,
        }

    @router.post("/v2/track/heartbeat")
    @router.post("/v1/jobs/progress")
    async def track_heartbeat(
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        name, key_id = authenticate_fn(authorization)
        check_rate_fn(name)
        body = await _read_json_or_gzip(request)
        if not isinstance(body, dict):
            raise HTTPException(400, "Expected JSON object")

        hdr_ver, hdr_plat = client_version_fn(request)
        hdr_mid = _safe_header(request, "x-zeno-machine-id", 80)
        ks = store_fn()
        email = _resolve_email(ks, key_id, name, str(body.get("editor_email") or ""))
        if hdr_ver and key_id is not None:
            note_version_fn(key_id, name, hdr_ver, hdr_plat, "heartbeat")

        prog = body.get("progress") if isinstance(body.get("progress"), dict) else body
        machine_id = str(body.get("machine_id") or hdr_mid or "default")[:80]
        platform = str(body.get("platform") or hdr_plat or "")[:80]
        ver = str(body.get("zeno_version") or hdr_ver or "")[:40]
        pending = body.get("pending_outbox")

        ks.upsert_machine_heartbeat(
            email,
            name,
            machine_id,
            platform=platform,
            zeno_version=ver,
            verified_version=str(body.get("verified_version") or "")[:40],
            bundle_sha256=str(body.get("bundle_sha256") or "")[:80],
            auth_source=str(body.get("auth_source") or "")[:40],
            pending_outbox=int(pending) if pending is not None else None,
            seen_at=time.time(),
        )

        # If a live stage progress snapshot has a project/video, touch the active job so stuck detection stays fresh
        proj = str(prog.get("project") or body.get("project_name") or "").strip()
        vname = str(prog.get("video_name") or body.get("notion_name") or "").strip()
        jkey = str(body.get("job_key") or "").strip()
        stage = str(prog.get("stage_name") or prog.get("state") or body.get("stage") or "").strip()[:40]
        if jkey or proj or vname:
            try:
                ev_id = f"hb_{machine_id}_{int(time.time() * 10)}"
                ks.ingest_track_event(
                    key_id,
                    name,
                    email,
                    {
                        "event_id": ev_id,
                        "event_type": "heartbeat",
                        "job_key": jkey,
                        "machine_id": machine_id,
                        "zeno_version": ver,
                        "platform": platform,
                        "ts": time.time(),
                        "payload": {
                            "project_name": proj,
                            "notion_name": vname,
                            "stage": stage or "working",
                            "detail": str(prog.get("detail") or "")[:300],
                            "pct": prog.get("pct"),
                        },
                    },
                )
            except Exception:
                log.exception("heartbeat job touch failed")
        return {"ok": True}

    @router.get("/v2/track/summary")
    def track_summary(
        date: str = "",
        days: int = 1,
        editor: str = "",
        tz_offset: int = 19800,
        stuck_after_sec: int = 1800,
        authorization: Optional[str] = Header(None),
        x_admin_password: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        check_viewer_fn(authorization, x_admin_password)
        return store_fn().track_summary(
            date_str=date,
            days=days,
            editor=editor,
            tz_offset=tz_offset,
            stuck_after_sec=stuck_after_sec,
        )

    @router.get("/v2/track/jobs/{job_key}")
    def track_job_detail(
        job_key: str,
        authorization: Optional[str] = Header(None),
        x_admin_password: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        check_viewer_fn(authorization, x_admin_password)
        rec = store_fn().track_job_detail(job_key)
        if rec is None:
            raise HTTPException(404, "No such tracked video job")
        return {"ok": True, "job": rec}

    @router.get("/v2/track/errors")
    def track_errors(
        date: str = "",
        days: int = 1,
        category: str = "",
        editor: str = "",
        tz_offset: int = 19800,
        stuck_after_sec: int = 1800,
        authorization: Optional[str] = Header(None),
        x_admin_password: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        check_viewer_fn(authorization, x_admin_password)
        return store_fn().track_errors(
            date_str=date,
            days=days,
            category=category,
            editor=editor,
            tz_offset=tz_offset,
            stuck_after_sec=stuck_after_sec,
        )

    @router.get("/v2/track/fleet")
    def track_fleet(
        tz_offset: int = 19800,
        authorization: Optional[str] = Header(None),
        x_admin_password: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        check_viewer_fn(authorization, x_admin_password)
        return store_fn().track_fleet(tz_offset=tz_offset)

    return router
