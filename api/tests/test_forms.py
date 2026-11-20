from urllib.parse import parse_qsl

import pytest

from app import errors, forms


def parse(s):
    return forms.parse_params(parse_qsl(s, keep_blank_values=True))


@pytest.mark.parametrize(
    ("qs", "expected"),
    [
        ("a=1", {"a": "1"}),
        ("a[b]=1", {"a": {"b": "1"}}),
        ("a[]=x&a[]=y", {"a": ["x", "y"]}),
        ("a[0][n]=1&a[1][n]=2", {"a": [{"n": "1"}, {"n": "2"}]}),
        ("a=", {"a": ""}),
        ("f=%26", {"f": "&"}),
    ],
    ids=["scalar", "nested", "array", "indexed_objects", "blank", "encoded_ampersand"],
)
def test_parse_params_accepts(qs, expected):
    assert parse(qs) == expected


@pytest.mark.parametrize(
    "qs",
    [
        "a=1&a=2",
        "a=1&a[b]=2",
        "a[b=1",
        "a[0][n]=1&a[2][n]=2",
        "a[b][c][d][e][f][g]=1",
        "&".join(f"k{i}=1" for i in range(1001)),
    ],
    ids=[
        "repeated_scalar",
        "scalar_mixed_with_brackets",
        "unbalanced_brackets",
        "non_contiguous_index",
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
