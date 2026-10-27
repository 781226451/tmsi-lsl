"""CLI behaviour that does not need a device (simulation mode)."""

from __future__ import annotations

import pytest

from tmsi_lsl.cli import EXIT_OK, EXIT_USAGE, build_parser, main


def test_parser_defaults_are_conservative():
    args = build_parser().parse_args([])
    assert args.lsl_name == "TMSi SAGA"
    assert args.dr_interface == "docked"
    assert args.ds_interface == "usb"
    assert args.duration == 0.0
    assert args.channels is None  # do not touch the device configuration


def test_list_channels_prints_the_device_table(capsys):
    exit_code = main(["--simulate", "--list-channels", "--sim-channels", "3"])
    assert exit_code == EXIT_OK

    output = capsys.readouterr().out
    assert "NAME" in output
    assert "UNI 01" in output
    assert "TRIGGER" in output


def test_unknown_channel_number_is_a_usage_error(caplog):
    exit_code = main(["--simulate", "--channels", "999"])
    assert exit_code == EXIT_USAGE
    assert "do not exist" in caplog.text


def test_bad_channel_syntax_is_a_usage_error(caplog):
    exit_code = main(["--simulate", "--channels", "8-3"])
    assert exit_code == EXIT_USAGE
    assert "end is before start" in caplog.text


def test_simulated_run_streams_and_exits_cleanly(capsys):
    exit_code = main(
        [
            "--simulate",
            "--sim-channels",
            "4",
            "--channels",
            "2-3",
            "--duration",
            "1.0",
            "--status-interval",
            "0",
        ]
    )
    assert exit_code == EXIT_OK

    output = capsys.readouterr().out
    assert "LSL stream" in output
    assert "Streamed" in output
    assert "frames" in output


def test_preset_ref64_selects_reference_plus_data_channels(capsys):
    exit_code = main(
        [
            "--simulate",
            "--sim-channels",
            "8",
            "--preset",
            "ref64",
            "--duration",
            "0.5",
            "--status-interval",
            "0",
        ]
    )
    assert exit_code == EXIT_OK

    output = capsys.readouterr().out
    # 8 UNI data channels + CREF; the request for 64 is clamped, not fatal.
    assert "9 channel(s)" in output


def test_preset_and_channels_are_mutually_exclusive():
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args(["--preset", "ref64", "--channels", "1-4"])
    assert excinfo.value.code == 2
