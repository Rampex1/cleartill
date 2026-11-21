import logging
from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app import config, errors, frontend
from app.db import get_db
from app.routers import (
    balance,
    charges,
    checkout,
    checkout_sessions,
    customers,
    dashboard,
    events,
    payment_intents,
    payment_methods,
    payouts,
    refunds,
    webhook_endpoints,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")
config.secret_key()  # refuse to start without a strong SECRET_KEY
config.public_base_url()  # and without a usable PUBLIC_BASE_URL
config.warn_if_insecure_urls(logging.getLogger("cleartill"))

app = FastAPI(title="Cleartill")
errors.install(app)
app.include_router(customers.router)
app.include_router(dashboard.router)
app.include_router(balance.router)
app.include_router(events.router)
app.include_router(payment_methods.router)
app.include_router(payment_intents.router)
app.include_router(charges.router)
app.include_router(refunds.router)
app.include_router(webhook_endpoints.router)
app.include_router(checkout.router)
app.include_router(checkout_sessions.router)
app.include_router(payouts.router)
frontend.install(app)  # after the routers: the SPA is the lowest-priority route


@app.get("/healthz")
def healthz(db: Annotated[Session, Depends(get_db)]):
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return JSONResponse({"status": "unavailable"}, status_code=503)
    return {"status": "ok"}
