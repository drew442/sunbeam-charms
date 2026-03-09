# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests for lvm-san-backend relation library."""

from pathlib import Path

import ops
import ops.testing
import pytest

import sys

sys.path.append(str(Path(__file__).parents[2] / "lib"))

from charms.lvm_san.v0.lvm_san_backend import (  # noqa:E402
    DataValidationError,
    LvmSanBackendData,
    LvmSanBackendProvides,
    LvmSanBackendRequires,
)


class ProviderCharm(ops.CharmBase):
    def __init__(self, framework):
        super().__init__(framework)
        self.interface = LvmSanBackendProvides(self, "lvm-san-backend")


class RequirerCharm(ops.CharmBase):
    def __init__(self, framework):
        super().__init__(framework)
        self.interface = LvmSanBackendRequires(self, "lvm-san-backend")


PROVIDER_META = """
name: lvm-san
provides:
  lvm-san-backend:
    interface: lvm-san-backend
"""

REQUIRER_META = """
name: cinder-volume-lvm-san
requires:
  lvm-san-backend:
    interface: lvm-san-backend
"""


@pytest.fixture
def provider_harness():
    harness = ops.testing.Harness(ProviderCharm, meta=PROVIDER_META)
    harness.begin()
    yield harness
    harness.cleanup()


@pytest.fixture
def requirer_harness():
    harness = ops.testing.Harness(RequirerCharm, meta=REQUIRER_META)
    harness.begin()
    yield harness
    harness.cleanup()


def test_schema_validation_rejects_invalid_vips() -> None:
    with pytest.raises(DataValidationError):
        LvmSanBackendData(
            backend_key="lvm-san.backend-a",
            vips=("not-an-ip",),
            ready=True,
            status="ready",
        ).to_relation_data()


def test_schema_validation_requires_chap_secret_references() -> None:
    with pytest.raises(DataValidationError):
        LvmSanBackendData(
            backend_key="lvm-san.backend-a",
            vips=("192.0.2.10",),
            ready=True,
            status="ready",
            auth_type="chap",
        ).to_relation_data()


def test_provider_requirer_round_trip(provider_harness, requirer_harness) -> None:
    provider_harness.set_leader(True)
    provider_rel_id = provider_harness.add_relation(
        "lvm-san-backend", "cinder-volume-lvm-san"
    )
    provider_harness.add_relation_unit(
        provider_rel_id, "cinder-volume-lvm-san/0"
    )

    requirer_rel_id = requirer_harness.add_relation("lvm-san-backend", "lvm-san")
    requirer_harness.add_relation_unit(requirer_rel_id, "lvm-san/0")

    backend_data = LvmSanBackendData(
        backend_key="lvm-san.cinder-volume-lvm-san",
        vips=("192.0.2.10", "192.0.2.11"),
        portals=("192.0.2.10",),
        volume_group="cinder-volumes",
        thin_pool="cinder-thinpool",
        target_helper="lioadm",
        ready=True,
        status="ready",
        active_node="lvh03.ntl1",
    )

    provider_harness.charm.interface.set_backend_data(backend_data, provider_rel_id)
    app_data = provider_harness.get_relation_data(
        provider_rel_id, provider_harness.charm.app.name
    )
    requirer_harness.update_relation_data(
        requirer_rel_id,
        "lvm-san",
        app_data,
    )

    parsed = requirer_harness.charm.interface.backend_data
    assert parsed is not None
    assert parsed.ready is True
    assert parsed.backend_key == "lvm-san.cinder-volume-lvm-san"
    assert parsed.vips == ("192.0.2.10", "192.0.2.11")
    assert parsed.portals == ("192.0.2.10",)
    assert parsed.volume_group == "cinder-volumes"
    assert parsed.active_node == "lvh03.ntl1"


def test_provider_does_not_publish_from_non_leader(provider_harness) -> None:
    rel_id = provider_harness.add_relation("lvm-san-backend", "cinder-volume-lvm-san")
    provider_harness.add_relation_unit(rel_id, "cinder-volume-lvm-san/0")
    provider_harness.set_leader(False)

    backend_data = LvmSanBackendData(
        backend_key="lvm-san.cinder-volume-lvm-san",
        vips=("192.0.2.10",),
        ready=True,
        status="ready",
    )
    provider_harness.charm.interface.set_backend_data(backend_data, rel_id)
    app_data = provider_harness.get_relation_data(rel_id, provider_harness.charm.app.name)
    assert app_data == {}
