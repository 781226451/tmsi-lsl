"""Unit tests for channel parsing and selection (no SDK, no hardware)."""

from __future__ import annotations

import pytest

from tmsi_lsl.channels import (
    ChannelInfo,
    SelectionError,
    format_channel_table,
    parse_channel_types,
    parse_spec,
    resolve_selection,
    select_indices,
    select_preset,
    select_reference_and_data,
    summarise_selection,
)


def make_channels() -> list[ChannelInfo]:
    """A small, SAGA-like channel list: CREF, 8 UNI, 2 BIP, 1 AUX, 1 status."""
    specs = [("CREF", "UNI"), ("UNI 01", "UNI"), ("UNI 02", "UNI"), ("UNI 03", "UNI")]
    specs += [(f"UNI {n:02d}", "UNI") for n in range(4, 9)]
    specs += [("BIP 01", "BIP"), ("BIP 02", "BIP"), ("AUX 01", "AUX"), ("TRIGGER", "STATUS")]

    return [
        ChannelInfo(
            index=index,
            name=name,
            channel_type=channel_type,
            unit="uV",
            divider=0,
            sample_rate=4000.0,
            is_active=True,
        )
        for index, (name, channel_type) in enumerate(specs)
    ]


# --------------------------------------------------------------------- parsing


def test_parse_spec_accepts_ranges_types_names_and_indices():
    selectors = parse_spec("1-4, type:AUX, name:BIP*, BIP 02")
    assert len(selectors) == 4


def test_parse_spec_ignores_whitespace_and_empty_items():
    assert len(parse_spec(" 1 ,, 3 ")) == 2


def test_parse_spec_single_number_is_one_channel():
    assert [selector.describe() for selector in parse_spec("7")] == ["7"]


@pytest.mark.parametrize("spec", ["", "   ", ",,,"])
def test_parse_spec_rejects_empty_selection(spec):
    with pytest.raises(SelectionError):
        parse_spec(spec)


def test_parse_spec_rejects_reversed_range():
    with pytest.raises(SelectionError, match="end is before start"):
        parse_spec("8-3")


def test_parse_spec_rejects_zero_based_numbers():
    with pytest.raises(SelectionError, match="1-based"):
        parse_spec("0")


def test_parse_channel_types_builds_type_selectors():
    selectors = parse_channel_types(" uni , bip ")
    assert [selector.describe() for selector in selectors] == ["type:UNI", "type:BIP"]


# --------------------------------------------------------------------- selection


def test_select_indices_by_number_is_one_based():
    channels = make_channels()
    assert select_indices(channels, parse_spec("1")) == [0]
    assert select_indices(channels, parse_spec("1-4")) == [0, 1, 2, 3]


def test_select_indices_by_type():
    channels = make_channels()
    assert select_indices(channels, parse_spec("type:BIP")) == [9, 10]


def test_select_indices_by_glob_is_case_insensitive():
    channels = make_channels()
    assert select_indices(channels, parse_spec("bip*")) == [9, 10]
    assert select_indices(channels, parse_spec("name:uni 0*")) == [1, 2, 3, 4, 5, 6, 7, 8]


def test_select_indices_handles_bare_names_with_spaces():
    channels = make_channels()
    assert select_indices(channels, parse_spec("UNI 03")) == [3]


def test_select_indices_deduplicates_and_sorts():
    channels = make_channels()
    indices = select_indices(channels, parse_spec("3, 1-3, type:AUX"))
    assert indices == [0, 1, 2, 11]


def test_select_indices_reports_out_of_range_numbers():
    channels = make_channels()
    with pytest.raises(SelectionError, match="do not exist"):
        select_indices(channels, parse_spec("99"))


def test_select_indices_reports_unknown_type():
    channels = make_channels()
    with pytest.raises(SelectionError, match="available types"):
        select_indices(channels, parse_spec("type:SENSOR"))


def test_select_indices_reports_unknown_name():
    channels = make_channels()
    with pytest.raises(SelectionError, match="no channel name matches"):
        select_indices(channels, parse_spec("EEG 42"))


def test_select_indices_rejects_empty_channel_list():
    with pytest.raises(SelectionError):
        select_indices([], parse_spec("1"))


# --------------------------------------------------------------------- rendering


def test_format_channel_table_contains_header_and_rows():
    table = format_channel_table(make_channels())
    lines = table.splitlines()
    assert lines[0].startswith("#") and "NAME" in lines[0]
    assert len(lines) == 2 + len(make_channels())
    assert "BIP 01" in table


def test_format_channel_table_handles_empty_list():
    assert format_channel_table([]) == "(device reported no channels)"


def test_summarise_selection_lists_short_and_elided_names():
    channels = make_channels()
    assert summarise_selection(channels, [0, 1]) == "2 channel(s): CREF, UNI 01"
    long = summarise_selection(channels, list(range(6)))
    assert long == "6 channel(s): CREF ... UNI 05"
    assert summarise_selection(channels, []) == "no channels"


# ------------------------------------------------------- saved selection replay


def test_resolve_selection_by_name_is_case_insensitive():
    channels = make_channels()
    indices, warnings = resolve_selection(channels, ["uni 02", "BIP 01"])
    assert indices == [2, 9]
    assert warnings == []


def test_resolve_selection_falls_back_to_saved_index():
    channels = make_channels()
    # "GONE" no longer exists; the saved index at that position is still valid.
    indices, warnings = resolve_selection(channels, ["UNI 01", "GONE"], [1, 4])
    assert indices == [1, 4]
    assert len(warnings) == 1 and "GONE" in warnings[0]


def test_resolve_selection_skips_unresolvable_names():
    channels = make_channels()
    indices, warnings = resolve_selection(channels, ["NOPE"], [999])
    assert indices == []
    assert len(warnings) == 1 and "skipped" in warnings[0]


def test_resolve_selection_uses_indices_when_no_names():
    channels = make_channels()
    indices, warnings = resolve_selection(channels, [], [0, 5, 500])
    assert indices == [0, 5]
    assert len(warnings) == 1 and "out of range" in warnings[0]


def test_resolve_selection_on_empty_device():
    assert resolve_selection([], ["UNI 01"], [1]) == ([], [])


# ----------------------------------------------------------------- presets


def make_uni_channels(data_channels: int = 64) -> list[ChannelInfo]:
    channels = [ChannelInfo(0, "CREF", "UNI", "uV", 0, 4000.0, True)]
    for number in range(1, data_channels + 1):
        channels.append(
            ChannelInfo(number, f"UNI {number:02d}", "UNI", "uV", 0, 4000.0, True)
        )
    channels.append(ChannelInfo(len(channels), "AUX 01", "AUX", "uV", 0, 4000.0, True))
    channels.append(
        ChannelInfo(len(channels), "COUNTER", "COUNTER", "-", 0, 4000.0, False)
    )
    return channels


def test_reference_plus_64_selects_reference_and_64_data_channels():
    channels = make_uni_channels(64)
    indices, warnings = select_reference_and_data(channels, 64)
    assert warnings == []
    assert len(indices) == 65
    assert indices[0] == 0  # CREF
    names = [channel.name for channel in channels if channel.index in set(indices)]
    assert names[0] == "CREF"
    assert names[1] == "UNI 01"
    assert names[-1] == "UNI 64"
    assert "AUX 01" not in names
    assert "COUNTER" not in names


def test_reference_plus_warns_when_not_enough_data_channels():
    channels = make_uni_channels(8)
    indices, warnings = select_reference_and_data(channels, 64)
    assert len(indices) == 9
    assert any("only 8 UNI data channels" in warning for warning in warnings)


def test_reference_plus_handles_missing_reference():
    channels = [channel for channel in make_uni_channels(4) if channel.name != "CREF"]
    indices, warnings = select_reference_and_data(channels, 64)
    assert len(indices) == 4
    assert any("no reference" in warning for warning in warnings)


def test_presets():
    channels = make_uni_channels(64)
    assert len(select_preset(channels, "ref64")[0]) == 65
    assert len(select_preset(channels, "uni")[0]) == 65   # CREF is UNI too
    assert len(select_preset(channels, "all")[0]) == 67

    with pytest.raises(SelectionError, match="unknown preset"):
        select_preset(channels, "nope")
