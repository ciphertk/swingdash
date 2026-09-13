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
        (["--help"], ["--help"]),
    ],
)
def test_bare_invocation_defaults_to_run(argv, expected):
    assert _with_default_command(argv) == expected


def test_watchlist_and_symbols_are_mutually_exclusive():
    with pytest.raises(SystemExit) as exit_info:
        main(["run", "-w", "a", "-s", "TCS"])
    assert exit_info.value.code == 2
