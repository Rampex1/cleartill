import logging
from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app import config, errors
from app.db import get_db
from app.routers import balance, customers, dashboard, events, payment_methods

logging.basicConfig(level=logging.INFO, format="%(message)s")
config.secret_key()  # refuse to start without a strong SECRET_KEY

app = FastAPI(title="Cleartill")
errors.install(app)
app.include_router(customers.router)
app.include_router(dashboard.router)
app.include_router(balance.router)
app.include_router(events.router)
app.include_router(payment_methods.router)


@app.get("/healthz")
def healthz(db: Annotated[Session, Depends(get_db)]):
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return JSONResponse({"status": "unavailable"}, status_code=503)
    return {"status": "ok"}
