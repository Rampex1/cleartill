import pytest
from sqlalchemy import update

from app import errors, seed, tenancy
from app.models import Customer


@pytest.fixture
def a(db):
    merchant, sk, _ = seed.seed(db, "a@example.com")
    return merchant, {"Authorization": f"Bearer {sk}"}


@pytest.fixture
def b(db):
    merchant, sk, _ = seed.seed(db, "b@example.com")
    return merchant, {"Authorization": f"Bearer {sk}"}


@pytest.fixture
def a_cus(client, a):
    r = client.post("/v1/customers", data={"name": "Ada", "metadata[k]": "v"}, headers=a[1])
    assert r.status_code == 200, r.text
    return r.json()


def assert_missing(r):
    assert r.status_code == 404
    assert r.json()["error"]["type"] == "invalid_request_error"
    assert r.json()["error"]["code"] == "resource_missing"


# --- API isolation -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "data"), [("GET", None), ("POST", {"name": "hacked"}), ("DELETE", None)]
)
def test_other_merchant_cannot_touch_customer(client, a, b, a_cus, method, data):
    assert_missing(client.request(method, f"/v1/customers/{a_cus['id']}", data=data, headers=b[1]))
    r = client.get(f"/v1/customers/{a_cus['id']}", headers=a[1])
    assert r.status_code == 200
    assert r.json() == a_cus


def test_other_merchant_list_excludes_customer(client, a, b, a_cus):
    r = client.get("/v1/customers", headers=b[1])
    assert r.status_code == 200
    assert a_cus["id"] not in [c["id"] for c in r.json()["data"]]


def test_other_merchant_cursor_is_404(client, a, b, a_cus):
    r = client.get("/v1/customers", params={"starting_after": a_cus["id"]}, headers=b[1])
    assert_missing(r)
    assert r.json()["error"]["param"] == "starting_after"


# --- get_owned -----------------------------------------------------------------------------


def test_get_owned_returns_row_for_owner(db, a, a_cus):
    row = tenancy.get_owned(db, Customer, a_cus["id"], a[0].id)
    assert row.id == a_cus["id"] and row.merchant_id == a[0].id


def test_get_owned_other_merchant_is_404(db, b, a_cus):
    with pytest.raises(errors.ApiError) as exc:
        tenancy.get_owned(db, Customer, a_cus["id"], b[0].id)
    e = exc.value
    assert (e.status, e.type, e.code, e.param) == (
        404,
        "invalid_request_error",
        "resource_missing",
        None,
    )


def test_get_owned_passes_param_through(db, b, a_cus):
    with pytest.raises(errors.ApiError) as exc:
        tenancy.get_owned(db, Customer, a_cus["id"], b[0].id, param="customer")
    assert exc.value.status == 404 and exc.value.param == "customer"


def test_get_owned_unknown_id_is_404(db, a):
    with pytest.raises(errors.ApiError) as exc:
        tenancy.get_owned(db, Customer, "cus_" + "0" * 24, a[0].id)
    assert exc.value.code == "resource_missing"


def test_get_owned_excludes_deleted_unless_asked(client, db, a, a_cus):
    client.delete(f"/v1/customers/{a_cus['id']}", headers=a[1])
    with pytest.raises(errors.ApiError) as exc:
        tenancy.get_owned(db, Customer, a_cus["id"], a[0].id)
    assert exc.value.status == 404
    row = tenancy.get_owned(db, Customer, a_cus["id"], a[0].id, include_deleted=True)
    assert row.deleted is True


def test_get_owned_include_deleted_still_checks_owner(client, db, a, b, a_cus):
    client.delete(f"/v1/customers/{a_cus['id']}", headers=a[1])
    with pytest.raises(errors.ApiError):
        tenancy.get_owned(db, Customer, a_cus["id"], b[0].id, include_deleted=True)


def test_get_owned_for_update_returns_fresh_values(db, a, a_cus):
    cid = a_cus["id"]
    assert tenancy.get_owned(db, Customer, cid, a[0].id).name == "Ada"
    # synchronize_session=False leaves the cached object stale, so only a re-read sees "changed"
    db.execute(
        update(Customer)
        .where(Customer.id == cid)
        .values(name="changed")
        .execution_options(synchronize_session=False)
    )
    assert tenancy.get_owned(db, Customer, cid, a[0].id, for_update=True).name == "changed"
