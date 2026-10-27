"""Tests for the TOML settings layer (no Qt involved)."""

from __future__ import annotations

import pytest

from tmsi_lsl.settings import (
    AppSettings,
    ChannelSettings,
    DeviceSettings,
    LslSettings,
    SamplingSettings,
    SettingsError,
)


def test_defaults_are_sane():
    settings = AppSettings()
    assert settings.device == DeviceSettings("docked", "usb", 0)
    assert settings.sampling == SamplingSettings(4000, 1)
    assert settings.channels == ChannelSettings((), ())
    assert settings.lsl.name == "TMSi SAGA"
    assert settings.sampling.effective_rate == 4000


def test_effective_rate_follows_divider():
    assert SamplingSettings(4096, 1).effective_rate == 4096
    assert SamplingSettings(4096, 8).effective_rate == 512
    assert SamplingSettings(4000, 4).effective_rate == 1000


def test_toml_text_has_sections_and_comments():
    text = AppSettings().to_toml()
    for section in ("[device]", "[channels]", "[sampling]", "[lsl]"):
        assert section in text
    assert "# tmsi-lsl settings" in text
    assert "schema_version = 1" in text


def test_round_trip_through_file(tmp_path):
    original = (
        AppSettings()
        .with_device(dr_interface="wifi", ds_interface="usb", device_index=2)
        .with_channels(["UNI 01", 'BIP "02"', "a\\b"], [1, 10, 11])
        .with_sampling(4096, 4)
        .with_lsl(name="EEG Lab A", offset=0.05)
    )
    path = tmp_path / "nested" / "settings.toml"
    written = original.save(path)

    assert written == path
    assert path.is_file()
    assert AppSettings.load(path) == original


def test_unknown_keys_are_ignored():
    text = """
    schema_version = 1
    future_flag = true

    [device]
    dr_interface = "optical"
    something_new = "x"

    [unknown_section]
    value = 1
    """
    settings = AppSettings.from_toml(text)
    assert settings.device.dr_interface == "optical"
    assert settings.sampling == SamplingSettings(4000, 1)


def test_partial_file_falls_back_to_defaults():
    settings = AppSettings.from_toml("[sampling]\nbase_rate = 4096\n")
    assert settings.sampling.base_rate == 4096
    assert settings.sampling.divider == 1
    assert settings.lsl.name == "TMSi SAGA"


@pytest.mark.parametrize(
    "text, message",
    [
        ("[sampling]\nbase_rate = 3000\n", "base_rate"),
        ("[sampling]\ndivider = 3\n", "divider"),
        ('[device]\ndr_interface = "serial"\n', "dr_interface"),
        ('[device]\nds_interface = "pci"\n', "ds_interface"),
        ('[lsl]\nname = "  "\n', "lsl.name"),
        ("[lsl]\noffset = 9.0\n", "lsl.offset"),
        ("[device]\ndevice_index = -1\n", "device_index"),
        ("[channels]\nindices = [-2]\n", "indices"),
        ('[channels]\nnames = "UNI 01"\n', "array of strings"),
        ("[channels]\nindices = [1.5]\n", "array of integers"),
        ("schema_version = 99\n", "newer"),
        ("not toml at all =", "not valid TOML"),
    ],
)
def test_invalid_values_are_rejected(text, message):
    with pytest.raises(SettingsError, match=message):
        AppSettings.from_toml(text, source="test.toml")


def test_missing_file_raises_settings_error(tmp_path):
    with pytest.raises(SettingsError, match="cannot read"):
        AppSettings.load(tmp_path / "nope.toml")


def test_with_helpers_return_new_instances():
    base = AppSettings()
    changed = base.with_sampling(4096, 2)
    assert base.sampling.base_rate == 4000  # untouched
    assert changed.sampling.base_rate == 4096
    assert changed.device == base.device
    assert isinstance(base.device, DeviceSettings)
    assert isinstance(base.lsl, LslSettings)
