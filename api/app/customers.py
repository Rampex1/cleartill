"""Customer JSON shapes, shared by the customers router, events and expand."""

from app.models import Customer


def customer_json(c: Customer) -> dict:
    return {
        "id": c.id,
        "object": "customer",
        "created": int(c.created.timestamp()),
        "livemode": False,
        "email": c.email,
        "name": c.name,
        "metadata": c.metadata_,
    }


def deleted_json(c: Customer) -> dict:
    return {"id": c.id, "object": "customer", "deleted": True}
