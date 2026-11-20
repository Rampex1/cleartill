from urllib.parse import parse_qsl

import pytest
from pydantic import BaseModel, ValidationError

from app import errors, forms


def parse(s):
    return forms.parse_params(parse_qsl(s, keep_blank_values=True))


@pytest.mark.parametrize(
    ("qs", "expected"),
    [
        ("a=1", {"a": "1"}),
        ("a[b]=1", {"a": {"b": "1"}}),
        ("a[]=x&a[]=y", {"a": ["x", "y"]}),
        # Digit keys stay a dict here; array fields convert them via FormList (below).
        ("a[0][n]=1&a[1][n]=2", {"a": {"0": {"n": "1"}, "1": {"n": "2"}}}),
        ("metadata[12345]=x&metadata[0]=y", {"metadata": {"12345": "x", "0": "y"}}),
        ("a=", {"a": ""}),
        ("f=%26", {"f": "&"}),
    ],
    ids=[
        "scalar",
        "nested",
        "array",
        "indexed_objects",
        "numeric_hash_keys",
        "blank",
        "encoded_ampersand",
    ],
)
def test_parse_params_accepts(qs, expected):
    assert parse(qs) == expected


@pytest.mark.parametrize(
    "qs",
    [
        "a=1&a=2",
        "a=1&a[b]=2",
        "a[b=1",
        "a[b][c][d][e][f][g]=1",
        "&".join(f"k{i}=1" for i in range(1001)),
    ],
    ids=[
        "repeated_scalar",
        "scalar_mixed_with_brackets",
        "unbalanced_brackets",
        "too_deep",
        "too_many_pairs",
    ],
)
def test_parse_params_rejects(qs):
    with pytest.raises(errors.ApiError) as exc:
        parse(qs)
    assert exc.value.status == 400
    assert exc.value.code == "parameter_invalid"
    assert exc.value.param


# FormList: array fields built from indexed form params (user-approved change, M3a review #1).


class _Item(BaseModel):
    n: int


class _WithList(BaseModel):
    items: forms.FormList[_Item] = []
    tags: forms.FormList[str] = []
    metadata: dict[str, str] = {}


def _model(qs):
    return _WithList.model_validate(forms.parse_params(parse_qsl(qs, keep_blank_values=True)))


def test_formlist_converts_indexed_dicts_in_order():
    m = _model("items[1][n]=2&items[0][n]=1")
    assert [i.n for i in m.items] == [1, 2]


def test_formlist_accepts_bracket_arrays():
    assert _model("tags[]=a&tags[]=b").tags == ["a", "b"]


@pytest.mark.parametrize("qs", ["items[0][n]=1&items[2][n]=2", "items[x][n]=1"])
def test_formlist_rejects_non_contiguous_or_named_indexes(qs):
    with pytest.raises(ValidationError) as exc:
        _model(qs)
    assert exc.value.errors()[0]["loc"][0] == "items"


def test_numeric_metadata_keys_stay_a_hash():
    assert _model("metadata[12345]=x&metadata[0]=y").metadata == {"12345": "x", "0": "y"}
