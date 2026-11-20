from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import StringConstraints
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import forms
from app.auth import AuthContext, require_secret_key
from app.db import get_db
from app.events import event_json, pending_counts
from app.models import Event
from app.tenancy import get_owned, paginate

router = APIRouter(prefix="/v1/events")

Auth = Annotated[AuthContext, Depends(require_secret_key)]
Db = Annotated[Session, Depends(get_db)]
Params = Annotated[dict, Depends(forms.params)]


class ListEventsParams(forms.ListParams):
    type: Annotated[str, StringConstraints(max_length=100)] | None = None


@router.get("")
def list_events(auth: Auth, db: Db, params: Params):
    lp = forms.validate(ListEventsParams, params)
    stmt = select(Event)
    if lp.type:
        stmt = stmt.where(Event.type == lp.type)
    page = paginate(db, stmt, Event, auth.merchant.id, lp, "/v1/events")
    counts = pending_counts(db, [e.id for e in page["data"]])
    page["data"] = [event_json(e, counts.get(e.id, 0)) for e in page["data"]]
    return page


@router.get("/{event_id}")
def retrieve_event(event_id: str, auth: Auth, db: Db, params: Params):
    forms.validate(forms.NoParams, params)
    event = get_owned(db, Event, event_id, auth.merchant.id)
    return event_json(event, pending_counts(db, [event.id]).get(event.id, 0))
