# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests for cinder-volume-lvm-san charm."""

from pathlib import Path
import sys
from unittest.mock import MagicMock, Mock, patch

ROOT = Path(__file__).parents[5]
sys.path.append(str(Path(__file__).parents[2] / "src"))
sys.path.append(str(Path(__file__).parents[2] / "lib"))
sys.path.append(str(ROOT / "ops-sunbeam"))

import charm
import ops.testing
import ops_sunbeam.test_utils as test_utils


class _CinderVolumeLVMSANOperatorCharm(charm.CinderVolumeLVMSANOperatorCharm):
    """Charm wrapper for test usage."""

    def __init__(self, framework):
        self.seen_events = []
        super().__init__(framework)

    def _log_event(self, event):
        self.seen_events.append(type(event).__name__)


def add_complete_cinder_volume_relation(harness: ops.testing.Harness) -> int:
    """Add a complete cinder-volume relation to the charm."""
    return harness.add_relation(
        "cinder-volume",
        "cinder-volume",
        unit_data={
            "snap-name": "cinder-volume",
        },
    )


def add_lvm_san_relation(harness: ops.testing.Harness, ready: bool = True) -> int:
    """Add a complete lvm-san-backend relation to the charm."""
    relation_id = harness.add_relation("lvm-san-backend", "lvm-san")
    harness.add_relation_unit(relation_id, "lvm-san/0")
    harness.update_relation_data(
        relation_id,
        "lvm-san",
        {
            "backend-key": "lvm-san.cinder-volume-lvm-san",
            "vips": '["192.0.2.20", "192.0.2.21"]',
            "portals": '["192.0.2.20"]',
            "volume-group": "cinder-volumes",
            "thin-pool": "cinder-thin",
            "target-helper": "lioadm",
            "target-protocol": "iscsi",
            "auth-type": "none",
            "ready": str(ready).lower(),
            "status": "ready" if ready else "waiting for fencing",
        },
    )
    return relation_id


class TestCinderVolumeLVMSANOperatorCharm(test_utils.CharmTestCase):
    """Test cases for CinderVolumeLVMSANOperatorCharm class."""

    PATCHES = []

    def setUp(self):
        """Setup fixtures ready for testing."""
        super().setUp(charm, self.PATCHES)
        self.mock_event = MagicMock()
        self.snap = Mock()
        snap_patch = patch.object(
            _CinderVolumeLVMSANOperatorCharm,
            "_import_snap",
            Mock(return_value=self.snap),
        )
        snap_patch.start()
        self.harness = test_utils.get_harness(
            _CinderVolumeLVMSANOperatorCharm,
            container_calls=self.container_calls,
        )

        mock_get_platform = patch(
            "charmhelpers.osplatform.get_platform", return_value="ubuntu"
        )
        mock_get_platform.start()

        self.addCleanup(mock_get_platform.stop)
        self.addCleanup(snap_patch.stop)
        self.addCleanup(self.harness.cleanup)

    def test_all_relations_ready(self):
        """Charm reports mandatory relations ready with complete data."""
        self.harness.begin_with_initial_hooks()
        add_lvm_san_relation(self.harness, ready=True)
        add_complete_cinder_volume_relation(self.harness)
        self.assertSetEqual(
            self.harness.charm.get_mandatory_relations_not_ready(self.mock_event),
            set(),
        )

    def test_backend_mapping_to_snap_config(self):
        """Backend configuration is rendered from lvm-san relation payload."""
        cinder_volume_snap_mock = MagicMock()
        cinder_volume_snap_mock.present = False
        self.snap.SnapState.Latest = "latest"
        self.snap.SnapCache.return_value = {
            "cinder-volume": cinder_volume_snap_mock,
        }

        self.harness.begin_with_initial_hooks()
        add_lvm_san_relation(self.harness, ready=True)
        add_complete_cinder_volume_relation(self.harness)

        cinder_volume_snap_mock.set.assert_any_call(
            {
                "lvm-san.cinder-volume-lvm-san": {
                    "volume-driver": "cinder.volume.drivers.lvm.LVMVolumeDriver",
                    "volume-group": "cinder-volumes",
                    "target-protocol": "iscsi",
                    "target-helper": "lioadm",
                    "iscsi-ip-address": "192.0.2.20",
                    "volume-backend-name": "lvm-san.cinder-volume-lvm-san",
                    "backend-availability-zone": None,
                    "lvm-type": "thin",
                    "lvm-pool-name": "cinder-thin",
                }
            },
            typed=True,
        )

    def test_waits_until_lvm_san_is_ready(self):
        """Charm does not treat lvm-san-backend relation as ready when not ready."""
        self.harness.begin_with_initial_hooks()
        add_lvm_san_relation(self.harness, ready=False)
        add_complete_cinder_volume_relation(self.harness)
        self.assertSetEqual(
            self.harness.charm.get_mandatory_relations_not_ready(self.mock_event),
            {"lvm-san-backend"},
        )
