"""Channel inspection and selection ("选通通道") for TMSi SAGA devices.

This module deliberately does not import the TMSi SDK: it operates on plain
:class:`ChannelInfo` records so that the parsing/selection logic can be tested
on machines without a SAGA device or the TMSi driver.

Selection syntax
----------------
A selection spec is a comma separated list of tokens.  Tokens are matched
against the *full* channel list (the one printed by ``--list-channels``):

===============  ==========================================================
``3``            channel number 3 (1-based, as shown by ``--list-channels``)
``1-8``          channel numbers 1 through 8 (inclusive)
``type:UNI``     every channel whose type is ``UNI``
``name:BIP*``    every channel whose name matches the glob ``BIP*``
``BIP 01``       bare token: treated as a name glob (case insensitive)
===============  ==========================================================

Channel numbers are **1-based** because that is what users see in the listing
and in the TMSi tooling.  The SDK itself uses 0-based indices; conversion
happens inside :func:`select_indices`.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from typing import Iterable, Sequence

__all__ = [
    "ChannelInfo",
    "SelectionError",
    "parse_spec",
    "parse_channel_types",
    "select_indices",
    "select_reference_and_data",
    "select_preset",
    "resolve_selection",
    "format_channel_table",
    "summarise_selection",
    "PRESETS",
]

_TYPE_PREFIX = "type:"
_NAME_PREFIX = "name:"
_INDEX_TOKEN = re.compile(r"^(?P<first>\d+)(?:\s*-\s*(?P<last>\d+))?$")

#: Named selections exposed on the command line (``--preset``).
PRESETS = ("ref64", "uni", "all")

#: Channel names that identify the reference electrode on TMSi hardware.
_REFERENCE_NAMES = ("CREF", "REF")


class SelectionError(ValueError):
    """Raised for an invalid or unsatisfiable channel selection."""


@dataclass(frozen=True)
class ChannelInfo:
    """A single channel of the device, in *full* channel-list order."""

    index: int
    """0-based index into the device's full channel list."""

    name: str
    channel_type: str
    unit: str
    divider: int
    sample_rate: float | None
    is_active: bool

    @property
    def number(self) -> int:
        """1-based channel number as shown to the user."""
        return self.index + 1


@dataclass(frozen=True)
class _IndexSelector:
    first: int  # 1-based, inclusive
    last: int  # 1-based, inclusive

    def describe(self) -> str:
        if self.first == self.last:
            return str(self.first)
        return f"{self.first}-{self.last}"


@dataclass(frozen=True)
class _TypeSelector:
    type_name: str

    def describe(self) -> str:
        return f"type:{self.type_name}"


@dataclass(frozen=True)
class _NameSelector:
    pattern: str

    def describe(self) -> str:
        return f"name:{self.pattern}"


Selector = _IndexSelector | _TypeSelector | _NameSelector


def parse_spec(spec: str) -> list[Selector]:
    """Parse a selection spec into selectors.

    :param spec: e.g. ``"1-8, type:AUX, name:BIP*"``.
    :raises SelectionError: on an empty spec or a malformed numeric token.
    """
    selectors: list[Selector] = []
    for raw_token in spec.split(","):
        token = raw_token.strip()
        if not token:
            continue
        lowered = token.lower()
        if lowered.startswith(_TYPE_PREFIX):
            type_name = token[len(_TYPE_PREFIX) :].strip()
            if not type_name:
                raise SelectionError(f"missing channel type in token {token!r}")
            selectors.append(_TypeSelector(type_name.upper()))
        elif lowered.startswith(_NAME_PREFIX):
            pattern = token[len(_NAME_PREFIX) :].strip()
            if not pattern:
                raise SelectionError(f"missing name pattern in token {token!r}")
            selectors.append(_NameSelector(pattern))
        else:
            match = _INDEX_TOKEN.match(token)
            if match:
                first = int(match.group("first"))
                last = int(match.group("last") or first)
                if first < 1 or last < 1:
                    raise SelectionError(
                        f"channel numbers are 1-based, got {token!r}"
                    )
                if last < first:
                    raise SelectionError(
                        f"invalid range {token!r}: end is before start"
                    )
                selectors.append(_IndexSelector(first, last))
            else:
                # Bare token: allow names containing a hyphen/space, e.g. "BIP 01".
                selectors.append(_NameSelector(token))

    if not selectors:
        raise SelectionError("empty channel selection")
    return selectors


def parse_channel_types(value: str) -> list[Selector]:
    """Turn ``"uni, bip"`` into type selectors (sugar for ``type:...``)."""
    selectors: list[Selector] = []
    for raw in value.split(","):
        name = raw.strip()
        if not name:
            continue
        selectors.append(_TypeSelector(name.upper()))
    if not selectors:
        raise SelectionError("empty channel type list")
    return selectors


def _type_matches(channel_type: str, wanted: str) -> bool:
    normalised = channel_type.upper()
    if normalised.startswith("CHANNELTYPE."):
        normalised = normalised.split(".", 1)[1]
    return normalised == wanted


def _name_table(channels: Sequence[ChannelInfo], limit: int = 12) -> str:
    names = [f"{ch.number}:{ch.name}" for ch in channels]
    if len(names) > limit:
        shown = ", ".join(names[:limit])
        return f"{shown}, ... ({len(names) - limit} more)"
    return ", ".join(names)


def select_indices(
    channels: Sequence[ChannelInfo],
    selectors: Iterable[Selector],
) -> list[int]:
    """Resolve selectors against ``channels`` to 0-based device indices.

    The result is de-duplicated and sorted by channel index, which is the order
    in which the SAGA reports active channels.

    :raises SelectionError: when a token matches nothing.
    """
    channel_list = list(channels)
    if not channel_list:
        raise SelectionError("device reported no channels")

    by_number = {ch.number: ch for ch in channel_list}
    available_types = sorted({ch.channel_type for ch in channel_list})
    chosen: set[int] = set()

    for selector in selectors:
        if isinstance(selector, _IndexSelector):
            missing = [
                number
                for number in range(selector.first, selector.last + 1)
                if number not in by_number
            ]
            if missing:
                raise SelectionError(
                    "channel number(s) {} do not exist; the device has channels 1-{}".format(
                        ", ".join(str(n) for n in missing),
                        channel_list[-1].number,
                    )
                )
            chosen.update(
                by_number[number].index
                for number in range(selector.first, selector.last + 1)
            )

        elif isinstance(selector, _TypeSelector):
            matches = [
                ch for ch in channel_list if _type_matches(ch.channel_type, selector.type_name)
            ]
            if not matches:
                raise SelectionError(
                    f"no channel of type {selector.type_name!r}; "
                    f"available types: {', '.join(available_types)}"
                )
            chosen.update(ch.index for ch in matches)

        else:  # _NameSelector
            pattern = selector.pattern.casefold()
            matches = [
                ch for ch in channel_list if fnmatch.fnmatchcase(ch.name.casefold(), pattern)
            ]
            if not matches:
                raise SelectionError(
                    f"no channel name matches {selector.pattern!r}; "
                    f"channels: {_name_table(channel_list)}"
                )
            chosen.update(ch.index for ch in matches)

    return sorted(chosen)


def summarise_selection(
    channels: Sequence[ChannelInfo],
    selected_indices: Sequence[int],
) -> str:
    """One-line human description such as ``32 channels (UNI 01 ... UNI 32)``."""
    selected = [ch for ch in channels if ch.index in set(selected_indices)]
    if not selected:
        return "no channels"
    if len(selected) <= 4:
        names = ", ".join(ch.name for ch in selected)
    else:
        names = f"{selected[0].name} ... {selected[-1].name}"
    return f"{len(selected)} channel(s): {names}"


def format_channel_table(channels: Sequence[ChannelInfo]) -> str:
    """Render the channel list as a fixed-width table."""
    if not channels:
        return "(device reported no channels)"

    headers = ("#", "NAME", "TYPE", "UNIT", "RATE", "ACTIVE")
    rows = [
        (
            str(ch.number),
            ch.name,
            ch.channel_type,
            ch.unit or "-",
            "-" if ch.sample_rate is None else f"{ch.sample_rate:g}",
            "yes" if ch.is_active else "no",
        )
        for ch in channels
    ]

    widths = [len(header) for header in headers]
    for row in rows:
        for column, cell in enumerate(row):
            widths[column] = max(widths[column], len(cell))

    def render(cells: Sequence[str]) -> str:
        return "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(cells)).rstrip()

    lines = [render(headers), render(["-" * width for width in widths])]
    lines.extend(render(row) for row in rows)
    return "\n".join(lines)


def _reference_channel(channels: Sequence[ChannelInfo]) -> ChannelInfo | None:
    """Best-effort identification of the reference (CREF) channel."""
    for channel in channels:
        if channel.name.strip().upper() in _REFERENCE_NAMES:
            return channel
    for channel in channels:
        if channel.name.strip().upper().endswith("REF"):
            return channel
    return None


def select_reference_and_data(
    channels: Sequence[ChannelInfo],
    data_channels: int = 64,
) -> tuple[list[int], list[str]]:
    """Select the reference channel plus the first ``data_channels`` UNI channels.

    Typical SAGA configuration: CREF (the reference electrode) plus 64 signal
    channels, with AUX / sensor / counter channels left out.

    :return: ``(0-based indices, warnings)``.
    """
    channel_list = list(channels)
    if not channel_list:
        return [], []

    reference = _reference_channel(channel_list)
    data = [
        channel
        for channel in channel_list
        if channel is not reference and channel.channel_type.upper() == "UNI"
    ]

    chosen = sorted(
        ([reference.index] if reference is not None else [])
        + [channel.index for channel in data[:data_channels]]
    )

    warnings: list[str] = []
    if reference is None:
        warnings.append("no reference (CREF) channel found")
    if len(data) < data_channels:
        warnings.append(
            f"only {len(data)} UNI data channels available, "
            f"{data_channels} were requested"
        )
    return chosen, warnings


def select_preset(
    channels: Sequence[ChannelInfo],
    preset: str,
) -> tuple[list[int], list[str]]:
    """Resolve a named preset from :data:`PRESETS`.

    * ``ref64``  - reference channel + the first 64 UNI data channels
    * ``uni``    - every UNI channel (reference included)
    * ``all``    - every channel the device reports
    """
    key = preset.strip().lower()
    if key == "ref64":
        return select_reference_and_data(channels, 64)
    if key == "uni":
        return select_indices(channels, parse_spec("type:UNI")), []
    if key == "all":
        return sorted(channel.index for channel in channels), []
    raise SelectionError(
        f"unknown preset {preset!r}; choose one of {', '.join(PRESETS)}"
    )


def resolve_selection(
    channels: Sequence[ChannelInfo],
    names: Sequence[str] = (),
    indices: Sequence[int] = (),
) -> tuple[list[int], list[str]]:
    """Map a previously saved selection onto the current channel list.

    Names are the durable identity: they are matched exactly first and then
    case-insensitively.  A name that no longer exists falls back to the saved
    index at the same position (indices are saved in parallel with names), and
    is reported as a warning when that fallback is not usable either.

    :return: ``(0-based indices, warnings)``.
    """
    channel_list = list(channels)
    if not channel_list:
        return [], []

    by_name = {channel.name: channel.index for channel in channel_list}
    by_folded = {channel.name.casefold(): channel.index for channel in channel_list}
    valid_indices = {channel.index for channel in channel_list}

    resolved: set[int] = set()
    warnings: list[str] = []

    if names:
        for position, raw_name in enumerate(names):
            name = str(raw_name).strip()
            if not name:
                continue
            if name in by_name:
                resolved.add(by_name[name])
                continue
            folded = by_folded.get(name.casefold())
            if folded is not None:
                resolved.add(folded)
                continue
            fallback = indices[position] if position < len(indices) else None
            if fallback is not None and fallback in valid_indices:
                resolved.add(int(fallback))
                warnings.append(
                    f"channel {name!r} no longer exists; used index {fallback} instead"
                )
            else:
                warnings.append(f"channel {name!r} no longer exists; skipped")
    else:
        for index in indices:
            if index in valid_indices:
                resolved.add(int(index))
            else:
                warnings.append(f"channel index {index} is out of range; skipped")

    return sorted(resolved), warnings
