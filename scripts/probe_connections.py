"""Probe every SAGA interface combination and report the devices found.

Useful when ``tmsi-lsl`` reports "no SAGA device found": it shows whether the
amplifier is reachable through the docking station (``docked``), an optical
cable (``optical``) or Wi-Fi (``wifi``), and which docking-station link
(``usb`` / ``network``) is in use.

    uv run python scripts/probe_connections.py
    uv run python scripts/probe_connections.py --verbose
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tmsi_lsl import sdk_bootstrap  # noqa: E402

DR_INTERFACES = ("docked", "optical", "wifi")
DS_INTERFACES = ("usb", "network")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verbose", action="store_true", help="show SDK log output")
    args = parser.parse_args()

    sdk_bootstrap.ensure_sdk_importable()
    from TMSiSDK.device.tmsi_device_enums import (  # noqa: PLC0415
        DeviceInterfaceType,
        DeviceType,
    )
    from TMSiSDK.tmsi_errors.error import TMSiError  # noqa: PLC0415
    from TMSiSDK.tmsi_sdk import TMSiSDK  # noqa: PLC0415

    import logging

    logging.getLogger("TMSi").setLevel(logging.DEBUG if args.verbose else logging.ERROR)

    dll = sdk_bootstrap.dll_information()
    print(f"driver : {dll['path']} (available={dll['available']})")
    print(f"sdk    : {sdk_bootstrap.sdk_root()}")
    print()

    total = 0
    for dr_name in DR_INTERFACES:
        for ds_name in DS_INTERFACES:
            dr = DeviceInterfaceType[dr_name]
            ds = DeviceInterfaceType[ds_name]
            label = f"{dr_name:<8} / {ds_name:<8}"
            try:
                TMSiSDK().discover(
                    dev_type=DeviceType.saga,
                    dr_interface=dr,
                    ds_interface=ds,
                    num_retries=1,
                )
            except TMSiError as exc:
                print(f"{label}  error: {exc}".splitlines()[0])
                continue

            devices = [
                device
                for device in TMSiSDK().get_device_list(DeviceType.saga)
                if device.get_dr_interface() == dr
            ]
            total += len(devices)
            if not devices:
                print(f"{label}  no device")
                continue
            for device in devices:
                print(
                    f"{label}  FOUND {device.get_device_name()} "
                    f"serial={device.get_device_serial_number()} "
                    f"channels={device.get_num_channels()} "
                    f"state={device.get_device_state()}"
                )

    print()
    if total:
        print(f"{total} device record(s) found. Try: uv run tmsi-lsl --list-channels")
        return 0
    print(
        "No device found on any interface.\n"
        "Check: DR powered on, DR seated in the docking station, USB cable connected,\n"
        "the TMSi user application closed, and the driver installed."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
