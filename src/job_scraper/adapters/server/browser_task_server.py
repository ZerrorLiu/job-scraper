"""FastAPI adapter and durable outbox consumer for browser work."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Response, status
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, ConfigDict, Field

from job_scraper.adapters.storage.browser_task_store import (
    BrowserTaskStore,
    BrowserTaskStoreError,
    LostLeaseError,
    OutboxEvent,
)
from job_scraper.adapters.storage.portal_store import PortalStore
from job_scraper.integrations.browser_details import (
    BrowserDetailContractError,
    BrowserDetailResult,
    BrowserDetailTask,
    BrowserSearchResult,
)
from job_scraper.integrations.email_recommendations import EmailIngestConfig
from job_scraper.jobs.ingest_email_recommendations import (
    browser_search_tasks,
    import_browser_detail_batch,
)
from job_scraper.ports.resume_analysis import ResumeAnalyzer

BLOCK_REASONS = frozenset(
    {
        "login_required",
        "captcha",
        "access_denied",
        "page_unavailable",
        "unexpected_layout",
        "navigation_outside_allowlist",
    }
)


class EnrollmentBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_id: str = Field(min_length=1, max_length=128)


class HeartbeatBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lease_id: str = Field(min_length=1)


class ResultBody(BaseModel):
    model_config = ConfigDict(extra="allow")
    lease_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=8, max_length=128)
    status: Literal["complete", "blocked", "unavailable"]
    observed_at: datetime
    error: str | None = None


class BrowserTaskApplication:
    def __init__(self, store: BrowserTaskStore) -> None:
        self.store = store

    def authorize(self, authorization: Annotated[str | None, Header()] = None) -> None:
        prefix = "Bearer "
        if authorization is None or not authorization.startswith(prefix):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
        if not self.store.verify_device_token(authorization[len(prefix) :].strip()):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or revoked device token")


def create_app(
    *,
    store: BrowserTaskStore,
    portal_store: PortalStore | None = None,
    upload_root: Any = None,
    send_login: Callable[[str, str], None] | None = None,
    resume_analyzer: ResumeAnalyzer | None = None,
    google_oauth: Any = None,
    candidate_workspace: Any = None,
    job_db: Any = None,
    allowed_hosts: list[str] | None = None,
    secure_cookie: bool = True,
) -> FastAPI:
    """Build one tenant's API. Ingress selects this process before body parsing."""

    application = BrowserTaskApplication(store)
    app = FastAPI(title="Positions browser worker API", version="1.0.0")
    if allowed_hosts:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)

    @app.middleware("http")
    async def security_headers(request: Any, call_next: Callable[[Any], Any]) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "form-action 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'"
        )
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        return response

    @app.get("/healthz")
    def health() -> dict[str, object]:
        return {"ok": True, "contract_version": "1.0"}

    if portal_store is not None:
        from pathlib import Path

        from job_scraper.adapters.server.portal import create_portal_router

        if send_login is None:
            raise ValueError("send_login is required when the portal is enabled")
        app.include_router(
            create_portal_router(
                portal_store=portal_store,
                browser_store=store,
                upload_root=Path(upload_root),
                send_login=send_login,
                analyzer=resume_analyzer,
                google_oauth=google_oauth,
                candidate_workspace=(Path(candidate_workspace) if candidate_workspace else None),
                job_db=(Path(job_db) if job_db else None),
                secure_cookie=secure_cookie,
            )
        )

    @app.get("/v1/instance")
    def instance() -> dict[str, str]:
        return {"client_id": store.instance_id(), "contract_version": "1.0"}

    @app.exception_handler(BrowserDetailContractError)
    async def contract_error(_request: Any, exc: BrowserDetailContractError) -> Response:
        return _json_error(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))

    @app.exception_handler(LostLeaseError)
    async def lease_error(_request: Any, exc: LostLeaseError) -> Response:
        return _json_error(status.HTTP_409_CONFLICT, str(exc))

    @app.exception_handler(BrowserTaskStoreError)
    async def store_error(_request: Any, exc: BrowserTaskStoreError) -> Response:
        return _json_error(status.HTTP_400_BAD_REQUEST, str(exc))

    @app.post("/v1/enrollments/redeem")
    def redeem(
        body: EnrollmentBody, authorization: Annotated[str | None, Header()] = None
    ) -> dict[str, str]:
        prefix = "Enrollment "
        if authorization is None or not authorization.startswith(prefix):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing enrollment token")
        token = authorization[len(prefix) :].strip()
        device_token = store.redeem_enrollment_token(token, device_id=body.device_id)
        if portal_store is not None:
            with portal_store.connect() as connection:
                tenant = connection.execute("SELECT id FROM portal_tenants LIMIT 1").fetchone()
            if (
                tenant is not None
                and portal_store.state(str(tenant["id"])) == "integrations_configured"
            ):
                portal_store.advance(str(tenant["id"]), "connector_enrolled")
        return {
            "device_token": device_token,
            "client_id": store.instance_id(),
            "contract_version": "1.0",
        }

    @app.post(
        "/v1/browser/tasks/claim",
        dependencies=[Depends(application.authorize)],
        response_model=None,
    )
    def claim(kind: Literal["search", "detail"] = "detail", created_on: date | None = None) -> Any:
        claimed = store.claim(kind, created_on=created_on)
        if claimed is None:
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        return {
            "contract_version": "1.0",
            "schema_version": 1,
            "task_id": claimed.task_id,
            "lease_id": claimed.lease_id,
            "lease_expires_at": claimed.lease_expires_at,
            "kind": claimed.kind,
            "payload": claimed.payload,
        }

    @app.get("/v1/browser/doctor", dependencies=[Depends(application.authorize)])
    def doctor() -> dict[str, str]:
        return {"ok": "true", "contract_version": "1.0"}

    @app.get("/v1/browser/wake", dependencies=[Depends(application.authorize)])
    def wake() -> dict[str, object]:
        """Cheap poll used by the local agent before it starts Codex."""
        queue_status = store.status()
        tasks = queue_status["tasks"]
        assert isinstance(tasks, dict)
        pending = int(tasks.get("pending", 0))
        return {
            "contract_version": "1.0",
            "pending": pending > 0,
            "pending_count": pending,
            "oldest_pending_at": queue_status["oldest_pending_at"],
        }

    @app.post(
        "/v1/browser/tasks/{task_id}/heartbeat", dependencies=[Depends(application.authorize)]
    )
    def heartbeat(task_id: str, body: HeartbeatBody) -> dict[str, object]:
        return {"ok": True, "lease_expires_at": store.heartbeat(task_id, body.lease_id)}

    @app.post("/v1/browser/tasks/{task_id}/results", dependencies=[Depends(application.authorize)])
    def results(task_id: str, body: ResultBody) -> dict[str, object]:
        stored = store.get(task_id)
        if stored is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown task_id {task_id!r}")
        submitted = body.model_dump(mode="json", exclude_none=True)
        if body.status != "complete" and body.error not in BLOCK_REASONS:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, "Unknown terminal reason code"
            )
        payload = stored["payload"]
        assert isinstance(payload, dict)
        merged: dict[str, object] = {**payload, **submitted}
        if stored["kind"] == "search":
            validated = BrowserSearchResult.from_mapping(merged)
        else:
            validated = BrowserDetailResult.from_mapping(merged)
        outcome = store.complete(
            task_id,
            body.lease_id,
            idempotency_key=body.idempotency_key,
            status=validated.status,
            result=merged,
        )
        return {
            "status": outcome.status,
            "event_id": outcome.event_id,
            "replayed": outcome.replayed,
        }

    return app


def _json_error(code: int, message: str) -> Response:
    import json

    return Response(
        json.dumps({"detail": message}), status_code=code, media_type="application/json"
    )


def refresh_search_tasks(store: BrowserTaskStore, email_config: EmailIngestConfig) -> int:
    tasks = browser_search_tasks(email_config.track_config_paths, datetime.now(UTC))
    return sum(store.enqueue("search", task.task_id, task.to_dict()) for task in tasks)


def process_outbox_event(
    event: OutboxEvent,
    *,
    store: BrowserTaskStore,
    email_config: EmailIngestConfig,
    skip_notion: bool,
) -> None:
    """Apply one accepted result outside the request transaction."""
    try:
        if event.kind == "search":
            result = BrowserSearchResult.from_mapping(event.payload)
            if result.status == "complete":
                for card in result.cards:
                    task = BrowserDetailTask.from_search_card(card)
                    store.enqueue("detail", task.task_id, task.to_dict())
        else:
            result = BrowserDetailResult.from_mapping(event.payload)
            if result.status == "complete":
                import_browser_detail_batch([result], email_config, skip_notion=skip_notion)
        store.finish_outbox(event.event_id)
    except Exception as exc:
        store.fail_outbox(event.event_id, str(exc))
        raise


def drain_outbox(
    *,
    store: BrowserTaskStore,
    email_config: EmailIngestConfig,
    skip_notion: bool,
    limit: int = 100,
    on_error: Callable[[Exception], None] | None = None,
) -> tuple[int, int]:
    applied = failed = 0
    for _ in range(limit):
        event = store.claim_outbox()
        if event is None:
            break
        try:
            process_outbox_event(
                event, store=store, email_config=email_config, skip_notion=skip_notion
            )
            applied += 1
        except Exception as exc:
            failed += 1
            if on_error:
                on_error(exc)
    return applied, failed
