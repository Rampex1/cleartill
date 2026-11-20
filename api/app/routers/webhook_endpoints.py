from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app import forms
from app.auth import AuthContext, require_secret_key
from app.db import get_db
from app.errors import ApiError
from app.events import KNOWN_EVENT_TYPES
from app.executor import execute
from app.ids import new_id, random_string
from app.models import WebhookDelivery, WebhookEndpoint
from app.tenancy import get_owned, paginate
from app.urls import validate_webhook_url

router = APIRouter(prefix="/v1/webhook_endpoints")

Auth = Annotated[AuthContext, Depends(require_secret_key)]
Db = Annotated[Session, Depends(get_db)]
Params = Annotated[dict, Depends(forms.params)]

EventList = Annotated[forms.FormList[str], Field(min_length=1, max_length=50)]
Description = Annotated[str, StringConstraints(max_length=500)]


class CreateParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    enabled_events: EventList
    description: Description | None = None


class UpdateParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str | None = None
    enabled_events: EventList | None = None
    description: Description | None = None
    disabled: bool | None = None


def endpoint_json(endpoint: WebhookEndpoint) -> dict:
    return {
        "id": endpoint.id,
        "object": "webhook_endpoint",
        "url": endpoint.url,
        "enabled_events": list(endpoint.enabled_events),
        "description": endpoint.description,
        "status": endpoint.status,
        "created": int(endpoint.created.timestamp()),
        "livemode": False,
    }


def _check_events(events: list[str]) -> list[str]:
    for event_type in events:
        if event_type != "*" and event_type not in KNOWN_EVENT_TYPES:
            raise ApiError(
                400,
                "invalid_request_error",
                "Unknown event type in enabled_events.",
                code="parameter_invalid",
                param="enabled_events",
            )
    return list(dict.fromkeys(events))


def _fail_pending_deliveries(db: Session, endpoint_id: str) -> None:
    db.execute(
        update(WebhookDelivery)
        .where(WebhookDelivery.endpoint_id == endpoint_id, WebhookDelivery.status == "pending")
        .values(status="failed")
    )


@router.post("")
def create_endpoint(request: Request, auth: Auth, db: Db, params: Params):
    def handler():
        data = forms.validate(CreateParams, params)
        validate_webhook_url(data.url)
        endpoint = WebhookEndpoint(
            id=new_id("we_"),
            merchant_id=auth.merchant.id,
            url=data.url,
            enabled_events=_check_events(data.enabled_events),
            description=data.description or None,
            secret="whsec_" + random_string(32),
            status="enabled",
        )
        db.add(endpoint)
        db.flush()
        return {**endpoint_json(endpoint), "secret": endpoint.secret}  # only at creation

    return execute(db, request, auth.merchant.id, params, handler)


@router.get("")
def list_endpoints(auth: Auth, db: Db, params: Params):
    lp = forms.validate(forms.ListParams, params)
    stmt = select(WebhookEndpoint).where(WebhookEndpoint.deleted.is_(False))
    page = paginate(db, stmt, WebhookEndpoint, auth.merchant.id, lp, "/v1/webhook_endpoints")
    page["data"] = [endpoint_json(e) for e in page["data"]]
    return page


@router.get("/{endpoint_id}")
def retrieve_endpoint(endpoint_id: str, auth: Auth, db: Db, params: Params):
    forms.validate(forms.NoParams, params)
    return endpoint_json(get_owned(db, WebhookEndpoint, endpoint_id, auth.merchant.id))


@router.post("/{endpoint_id}")
def update_endpoint(endpoint_id: str, request: Request, auth: Auth, db: Db, params: Params):
    def handler():
        data = forms.validate(UpdateParams, params)
        endpoint = get_owned(db, WebhookEndpoint, endpoint_id, auth.merchant.id, for_update=True)
        sent = data.model_fields_set
        if "url" in sent and data.url is not None:
            validate_webhook_url(data.url)
            endpoint.url = data.url
        if "enabled_events" in sent and data.enabled_events is not None:
            endpoint.enabled_events = _check_events(data.enabled_events)
        if "description" in sent:
            endpoint.description = data.description or None
        if "disabled" in sent and data.disabled is not None:
            endpoint.status = "disabled" if data.disabled else "enabled"
            if data.disabled:
                _fail_pending_deliveries(db, endpoint.id)
        db.flush()
        return endpoint_json(endpoint)

    return execute(db, request, auth.merchant.id, params, handler)


@router.delete("/{endpoint_id}")
def delete_endpoint(endpoint_id: str, request: Request, auth: Auth, db: Db, params: Params):
    def handler():
        forms.validate(forms.NoParams, params)
        endpoint = get_owned(db, WebhookEndpoint, endpoint_id, auth.merchant.id, for_update=True)
        endpoint.deleted = True
        _fail_pending_deliveries(db, endpoint.id)
        db.flush()
        return {"id": endpoint.id, "object": "webhook_endpoint", "deleted": True}

    return execute(db, request, auth.merchant.id, params, handler)
