"""Reference handling: the SAGA must stay on the CREF electrode reference.

``common`` references every UNI channel against the CREF electrode;
``average`` would replace that with a common average reference.  These tests pin
the behaviour on the simulator (no hardware needed).
"""

from __future__ import annotations

import pytest

from tmsi_lsl.saga_source import SagaSource, SourceError
from tmsi_lsl.simulator import SimulatedSaga


@pytest.fixture()
def source():
    saga = SagaSource()
    device = SimulatedSaga(num_uni=4)
    device.open()
    saga.attach(device)
    try:
        yield saga, device
    finally:
        saga.close()


def test_default_reference_is_common(source):
    saga, _device = source
    assert saga.reference() == {"reference": "common", "autoreference": "fixed"}


def test_ensure_reference_is_a_no_op_when_already_common(source):
    saga, device = source
    writes = []
    original = device.set_device_references
    device.set_device_references = lambda **kwargs: (writes.append(kwargs), original(**kwargs))[1]

    refs = saga.ensure_reference("common", auto="fixed")

    assert refs == {"reference": "common", "autoreference": "fixed"}
    assert writes == [], "an already-correct device must not be reconfigured"


def test_ensure_reference_switches_average_back_to_common(source):
    saga, device = source
    device.set_device_references(reference_method="Average", auto_reference_method="Average")
    assert saga.reference()["reference"] == "average"

    refs = saga.ensure_reference("common", auto="fixed")

    assert refs == {"reference": "common", "autoreference": "fixed"}
    assert saga.reference() == {"reference": "common", "autoreference": "fixed"}


def test_ensure_reference_rejects_unknown_values(source):
    saga, _device = source
    with pytest.raises(SourceError, match="unsupported reference method"):
        saga.ensure_reference("carl")
    with pytest.raises(SourceError, match="unsupported auto-reference"):
        saga.ensure_reference("common", auto="auto")


def test_reference_cannot_change_while_sampling(source):
    saga, device = source
    device.set_device_references(reference_method="Average")
    saga._sampling = True  # simulate an acquisition in progress
    try:
        with pytest.raises(SourceError, match="while sampling"):
            saga.ensure_reference("common")
    finally:
        saga._sampling = False
