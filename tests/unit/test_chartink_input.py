"""Everything a user might paste into the Chartink 'add' box."""

import pytest

from swingdash.domain.chartink import (
    ChartinkInputError,
    ChartinkKind,
    ChartinkRequest,
    ImportTarget,
    parse_user_input,
)

CLAUSE = "( {57960} ( latest sma( latest close , 5 ) < latest close * 1.03 ) )"


def _request(text: str) -> ChartinkRequest:
    parsed = parse_user_input(text)
    assert isinstance(parsed, ChartinkRequest)
    return parsed


@pytest.mark.parametrize(
    ("text", "kind", "url"),
    [
        (
            "https://chartink.com/screener/consolidatedbo",
            "screener",
            "https://chartink.com/screener/consolidatedbo",
        ),
        (
            "chartink.com/screener/x",  # no scheme
            None,
            None,
        ),
        (
            "https://www.chartink.com/dashboard/130216?tab=1",
            "dashboard",
            "https://chartink.com/dashboard/130216",
        ),
    ],
)
def test_links_become_import_targets(text, kind, url):
    if kind is None:
        # Without a scheme it isn't a link; it isn't a clause either.
        with pytest.raises(ChartinkInputError):
            parse_user_input(text)
        return
    parsed = parse_user_input(text)
    assert parsed == ImportTarget(kind, url, url.rsplit("/", 1)[1])


def test_other_links_are_rejected():
    with pytest.raises(ChartinkInputError, match=r"Only chartink\.com"):
        parse_user_input("https://tradingview.com/chart/")


def test_the_python_dict_from_a_script():
    request = _request(f"{{'scan_clause': '{CLAUSE}'}}")
    assert request == ChartinkRequest(ChartinkKind.SCREENER, {"scan_clause": CLAUSE})


def test_json_payload_keeps_every_screener_field():
    request = _request(
        '{"scan_clause": "' + CLAUSE + '", "debug_clause": "x", "column_clause": "y", "junk": 1}'
    )
    assert request.fields == {"scan_clause": CLAUSE, "debug_clause": "x", "column_clause": "y"}


def test_form_encoded_payload_from_devtools_view_source():
    text = "scan_clause=%28+%7B57960%7D+%28+latest+close+%3E+5+%29+%29&debug_clause=groupcount%28+1+%29"
    request = _request(text)
    assert request.kind is ChartinkKind.SCREENER
    assert request.fields["scan_clause"] == "( {57960} ( latest close > 5 ) )"
    assert request.fields["debug_clause"] == "groupcount( 1 )"


def test_field_lines_from_devtools_view_parsed():
    text = f"scan_clause: {CLAUSE}\ndebug_clause: groupcount( 1 )\n"
    assert _request(text).fields == {"scan_clause": CLAUSE, "debug_clause": "groupcount( 1 )"}


def test_a_widget_payload_always_asks_for_just_the_latest_values():
    text = "query=select+latest+Close+as+%27Close%27+WHERE+%7Bcash%7D+1+%3D+1&use_live=1&limit=50&size=100"
    request = _request(text)
    assert request.kind is ChartinkKind.WIDGET
    assert request.fields == {
        "query": "select latest Close as 'Close' WHERE {cash} 1 = 1",
        "use_live": "1",
        "limit": "50",  # what the user asked for
        "size": "1",  # only the latest bar is displayed
    }


@pytest.mark.parametrize(
    ("text", "field"),
    [(f"  {CLAUSE}\n", "scan_clause"), ("SELECT latest close as 'c'\nWHERE {cash} 1 = 1", "query")],
)
def test_bare_clauses_and_queries(text, field):
    request = _request(text)
    assert field in request.fields
    assert "\n" not in request.fields[field]


@pytest.mark.parametrize("text", ["", "   ", "hello there", "{'foo': 'bar'}", "limit=5"])
def test_nothing_runnable_is_rejected(text):
    with pytest.raises(ChartinkInputError):
        parse_user_input(text)


def test_a_screener_link_followed_by_its_payload():
    text = (
        "https://chartink.com/screener/total-universe-v2\n"
        '{"scan_clause": "( {cash} ( close > 15 ) )", '
        '"column_clause": " Daily Close as \'scan-column-default-close\'"}'
    )
    parsed = parse_user_input(text)
    assert isinstance(parsed, ImportTarget)
    assert parsed.url == "https://chartink.com/screener/total-universe-v2"
    assert parsed.payload is not None
    assert parsed.payload.kind is ChartinkKind.SCREENER
    assert parsed.payload.fields["column_clause"].strip().startswith("Daily Close")


@pytest.mark.parametrize(
    "text",
    [
        "https://chartink.com/dashboard/130216\n( {cash} ( close > 15 ) )",
        "https://chartink.com/screener/x\nselect close where {cash} ( 1 = 1 ) GROUP BY symbol",
        "https://chartink.com/screener/x\nnot a payload",
    ],
)
def test_a_link_followed_by_the_wrong_kind_of_paste_is_rejected(text):
    with pytest.raises(ChartinkInputError):
        parse_user_input(text)
