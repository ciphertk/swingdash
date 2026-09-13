import pytest

import swingdash
from swingdash.cli import _with_default_command, main


def test_version_is_resolved_from_installed_metadata():
    assert swingdash.__version__ != "0.0.0+unknown"


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])
    assert exit_info.value.code == 0
    assert swingdash.__version__ in capsys.readouterr().out


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        ([], ["run"]),
        (["-w", "nxtDay"], ["run", "-w", "nxtDay"]),
        (["run", "-s", "TCS"], ["run", "-s", "TCS"]),
        (["doctor", "--feed"], ["doctor", "--feed"]),
        (["refresh", "--no-sectors"], ["refresh", "--no-sectors"]),
        (["migrate-legacy", "--from", "."], ["migrate-legacy", "--from", "."]),
        (["--help"], ["--help"]),
    ],
)
def test_bare_invocation_defaults_to_run(argv, expected):
    assert _with_default_command(argv) == expected


def test_watchlist_and_symbols_are_mutually_exclusive():
    with pytest.raises(SystemExit) as exit_info:
        main(["run", "-w", "a", "-s", "TCS"])
    assert exit_info.value.code == 2


def test_run_without_a_token_explains_how_to_fix_it(capsys):
    assert main(["run"]) == 1
    assert "swingdash setup" in capsys.readouterr().out


def test_doctor_reports_problems_without_crashing(capsys):
    assert main(["doctor"]) == 1
    out = capsys.readouterr().out
    assert "token" in out
    assert "missing" in out
