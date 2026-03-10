"""Unit tests for lvm-san snap-bridge charm behavior."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import ops.testing

sys.path.append(str(Path(__file__).parents[2] / "src"))
sys.path.append(str(Path(__file__).parents[2] / "lib"))

import charm  # noqa:E402


META = """
name: lvm-san
provides:
  lvm-san-backend:
    interface: lvm-san-backend
"""

ACTIONS = """
reconcile-now:
show-snap-status:
san-check:
iscsi-login:
multipath-report:
pcs-status:
backend-move:
backend-clear-move:
install-snap:
install-prereqs:
configure-iscsi-initiator:
apply-multipath-config:
bootstrap-cluster:
set-node-hostname:
restart-cluster-services:
test-export-create:
test-export-delete:
test-export-show:
lvm-lv-create:
lvm-lv-remove:
lvm-lv-resize:
lvm-lv-activate:
lvm-lv-deactivate:
lvm-lv-rename:
lvm-snapshot-create:
lvm-snapshot-delete:
lvm-snapshot-revert:
lvm-lv-show:
"""


def _status_payload(
    *,
    ready: bool,
    fencing: bool = True,
    cluster: bool = True,
    portals: list[str] | None = None,
) -> dict:
    return {
        "api_version": "v1alpha1",
        "cluster_ready": cluster,
        "fencing_configured": fencing,
        "backends": [
            {
                "backend_key": "lvm-san.default",
                "ready": ready,
                "status": "ok" if ready else "waiting for vip",
                "preferred_active_unit": "lvm-san/1",
                "active_unit": "lvm-san/0",
                "portals": portals or [],
            }
        ],
    }


def test_publishes_ready_backend_data(tmp_path: Path) -> None:
    intent = tmp_path / "intent.json"
    status = tmp_path / "status.json"
    status.write_text(json.dumps(_status_payload(ready=True)))

    harness = ops.testing.Harness(charm.LVMSANCharm, meta=META, actions=ACTIONS)
    relation_id = harness.add_relation("lvm-san-backend", "cinder-volume-lvm-san")
    harness.add_relation_unit(relation_id, "cinder-volume-lvm-san/0")

    with patch("charm.subprocess.run") as run_mock:
        run_mock.return_value = subprocess.CompletedProcess([], 0, "", "")
        harness.begin()
        harness.set_leader()
        harness.update_config({
            "vips": "192.0.2.10",
            "intent-path": str(intent),
            "status-path": str(status),
        })

    app_data = harness.get_relation_data(relation_id, harness.charm.app.name)
    assert app_data["backend-key"] == "lvm-san.default"
    assert app_data["ready"] == "true"
    assert app_data["status"] == "ok"
    assert app_data["preferred-active-unit"] == "lvm-san/1"
    assert app_data["active-unit"] == "lvm-san/0"
    assert app_data["portals"] == '["192.0.2.10"]'
    harness.cleanup()


def test_blocks_when_fencing_not_configured(tmp_path: Path) -> None:
    intent = tmp_path / "intent.json"
    status = tmp_path / "status.json"
    status.write_text(json.dumps(_status_payload(ready=True, fencing=False, cluster=True)))

    harness = ops.testing.Harness(charm.LVMSANCharm, meta=META, actions=ACTIONS)
    relation_id = harness.add_relation("lvm-san-backend", "cinder-volume-lvm-san")
    harness.add_relation_unit(relation_id, "cinder-volume-lvm-san/0")

    with patch("charm.subprocess.run") as run_mock:
        run_mock.return_value = subprocess.CompletedProcess([], 0, "", "")
        harness.begin()
        harness.set_leader()
        harness.update_config({
            "vips": "192.0.2.10",
            "intent-path": str(intent),
            "status-path": str(status),
        })

    app_data = harness.get_relation_data(relation_id, harness.charm.app.name)
    assert app_data["ready"] == "false"
    assert app_data["status"] == "fencing not configured"
    assert harness.model.unit.status.name == "blocked"
    harness.cleanup()


def test_prefers_status_portals_over_config_portals(tmp_path: Path) -> None:
    intent = tmp_path / "intent.json"
    status = tmp_path / "status.json"
    status.write_text(json.dumps(_status_payload(ready=True, portals=["192.0.2.55"])))

    harness = ops.testing.Harness(charm.LVMSANCharm, meta=META, actions=ACTIONS)
    relation_id = harness.add_relation("lvm-san-backend", "cinder-volume-lvm-san")
    harness.add_relation_unit(relation_id, "cinder-volume-lvm-san/0")

    with patch("charm.subprocess.run") as run_mock:
        run_mock.return_value = subprocess.CompletedProcess([], 0, "", "")
        harness.begin()
        harness.set_leader()
        harness.update_config({
            "vips": "192.0.2.10",
            "portals": "192.0.2.20",
            "intent-path": str(intent),
            "status-path": str(status),
        })

    app_data = harness.get_relation_data(relation_id, harness.charm.app.name)
    assert app_data["portals"] == '["192.0.2.55"]'
    harness.cleanup()


def test_parse_target_portal_defaults_port() -> None:
    host, port = charm.LVMSANCharm._parse_target_portal("192.168.73.1")
    assert host == "192.168.73.1"
    assert port == "3260"


def test_parse_target_portal_supports_port() -> None:
    host, port = charm.LVMSANCharm._parse_target_portal("192.168.73.1:3261")
    assert host == "192.168.73.1"
    assert port == "3261"


def test_sanitize_backend_key_for_resource_name() -> None:
    assert (
        charm.LVMSANCharm._sanitize_resource_name("lvm-san.backend-a")
        == "lvm-san-backend-a"
    )


def test_nonfatal_iscsi_error_detection_for_already_present() -> None:
    assert charm.LVMSANCharm._is_nonfatal_iscsi_login_error(
        "iscsiadm: default: 1 session requested, but 1 already present."
    )


def test_nonfatal_iscsi_error_detection_for_other_errors() -> None:
    assert not charm.LVMSANCharm._is_nonfatal_iscsi_login_error(
        "iscsiadm: initiator reported error (20 - could not connect to target)"
    )


def test_pcs_online_nodes_filters_offline() -> None:
    status = (
        "Node List:\n"
        "  * Node juju-a-0 (1): online\n"
        "  * Node juju-a-1 (2): OFFLINE\n"
    )
    assert charm.LVMSANCharm._pcs_online_nodes(status) == ["juju-a-0"]


def test_resolve_target_node_passthrough_hostname(tmp_path: Path) -> None:
    intent = tmp_path / "intent.json"
    status = tmp_path / "status.json"
    status.write_text(json.dumps(_status_payload(ready=True)))
    harness = ops.testing.Harness(charm.LVMSANCharm, meta=META, actions=ACTIONS)
    with patch("charm.subprocess.run") as run_mock:
        run_mock.return_value = subprocess.CompletedProcess([], 0, "", "")
        harness.begin()
        harness.update_config({
            "vips": "192.0.2.10",
            "intent-path": str(intent),
            "status-path": str(status),
        })
        assert harness.charm._resolve_target_node("juju-abc-1") == "juju-abc-1"
    harness.cleanup()


def test_resolve_target_node_from_unit(tmp_path: Path) -> None:
    intent = tmp_path / "intent.json"
    status = tmp_path / "status.json"
    status.write_text(json.dumps(_status_payload(ready=True)))
    harness = ops.testing.Harness(charm.LVMSANCharm, meta=META, actions=ACTIONS)
    pcs_out = (
        "Node List:\n"
        "  * Node juju-4eb05b-0 (1): online\n"
        "  * Node juju-4eb05b-1 (2): online\n"
    )
    with patch("charm.subprocess.run") as run_mock:
        run_mock.side_effect = lambda args, **kwargs: subprocess.CompletedProcess(
            [],
            0,
            pcs_out if list(args[:3]) == ["pcs", "status", "--full"] else "",
            "",
        )
        harness.begin()
        harness.update_config({
            "vips": "192.0.2.10",
            "intent-path": str(intent),
            "status-path": str(status),
        })
        assert harness.charm._resolve_target_node("lvm-san/1") == "juju-4eb05b-1"
    harness.cleanup()


def test_resolve_target_node_from_two_node_cluster_non_suffix_names(tmp_path: Path) -> None:
    intent = tmp_path / "intent.json"
    status = tmp_path / "status.json"
    status.write_text(json.dumps(_status_payload(ready=True)))
    harness = ops.testing.Harness(charm.LVMSANCharm, meta=META, actions=ACTIONS)
    pcs_out = (
        "Node List:\n"
        "  * Node lvh03.ntl1 (1): online\n"
        "  * Node lvh01.ntl1 (2): online\n"
    )
    with (
        patch("charm.subprocess.run") as run_mock,
        patch("charm.socket.gethostname", return_value="lvh03"),
        patch("charm.socket.getfqdn", return_value="lvh03.ntl1"),
    ):
        run_mock.side_effect = lambda args, **kwargs: subprocess.CompletedProcess(
            [],
            0,
            pcs_out if list(args[:3]) == ["pcs", "status", "--full"] else "",
            "",
        )
        harness.begin()
        harness.update_config({
            "vips": "192.0.2.10",
            "intent-path": str(intent),
            "status-path": str(status),
        })
        assert harness.charm._resolve_target_node("lvm-san/1") == "lvh01.ntl1"
    harness.cleanup()


def test_full_lv_path_with_and_without_vg() -> None:
    assert charm.LVMSANCharm._full_lv_path("cinder-volumes-a", "my-lv") == "cinder-volumes-a/my-lv"
    assert charm.LVMSANCharm._full_lv_path("cinder-volumes-a", "other-vg/my-lv") == "other-vg/my-lv"


def test_active_backend_unit_name_from_status(tmp_path: Path) -> None:
    intent = tmp_path / "intent.json"
    status = tmp_path / "status.json"
    status.write_text(json.dumps(_status_payload(ready=True)))
    harness = ops.testing.Harness(charm.LVMSANCharm, meta=META, actions=ACTIONS)
    with patch("charm.subprocess.run") as run_mock:
        run_mock.return_value = subprocess.CompletedProcess([], 0, "", "")
        harness.begin()
        harness.update_config(
            {
                "vips": "192.0.2.10",
                "backend-key": "lvm-san.default",
                "intent-path": str(intent),
                "status-path": str(status),
            }
        )
        assert harness.charm._active_backend_unit_name() == "lvm-san/0"
    harness.cleanup()


def test_active_backend_node_name_from_pcs_group(tmp_path: Path) -> None:
    intent = tmp_path / "intent.json"
    status = tmp_path / "status.json"
    status.write_text(json.dumps(_status_payload(ready=True)))
    harness = ops.testing.Harness(charm.LVMSANCharm, meta=META, actions=ACTIONS)
    pcs_out = (
        "Full List of Resources:\n"
        "  * Resource Group: grp-lvm-san-default:\n"
        "    * vip-lvm-san-default-0 (ocf:heartbeat:IPaddr2): Started lvh01.ntl1\n"
    )
    with patch("charm.subprocess.run") as run_mock:
        run_mock.side_effect = lambda args, **kwargs: subprocess.CompletedProcess(
            [],
            0,
            pcs_out if list(args[:3]) == ["pcs", "status", "--full"] else "",
            "",
        )
        harness.begin()
        harness.update_config(
            {
                "vips": "192.0.2.10",
                "backend-key": "lvm-san.default",
                "intent-path": str(intent),
                "status-path": str(status),
            }
        )
        assert harness.charm._active_backend_node_name("lvm-san.default") == "lvh01.ntl1"
    harness.cleanup()


def test_resolve_backend_group_name_fallback_single_group(tmp_path: Path) -> None:
    intent = tmp_path / "intent.json"
    status = tmp_path / "status.json"
    status.write_text(json.dumps(_status_payload(ready=True)))
    harness = ops.testing.Harness(charm.LVMSANCharm, meta=META, actions=ACTIONS)
    pcs_out = (
        "Full List of Resources:\n"
        "  * Resource Group: grp-lvm-san-cinder-volume-lvm-san:\n"
        "    * vip-lvm-san-cinder-volume-lvm-san-0 (ocf:heartbeat:IPaddr2): Started lvh01.ntl1\n"
    )

    def _run(args, **kwargs):
        if list(args[:3]) == ["pcs", "resource", "show"]:
            return subprocess.CompletedProcess([], 1, "", "not found")
        if list(args[:3]) == ["pcs", "status", "--full"]:
            return subprocess.CompletedProcess([], 0, pcs_out, "")
        return subprocess.CompletedProcess([], 0, "", "")

    with patch("charm.subprocess.run", side_effect=_run):
        harness.begin()
        harness.update_config(
            {
                "vips": "192.0.2.10",
                "backend-key": "lvm-san.cinder-volume-lvm-san-noha",
                "intent-path": str(intent),
                "status-path": str(status),
            }
        )
        assert (
            harness.charm._resolve_backend_group_name(
                "lvm-san.cinder-volume-lvm-san-noha"
            )
            == "grp-lvm-san-cinder-volume-lvm-san"
        )
    harness.cleanup()


def test_lvm_snapshot_revert_rejects_thin_snapshot(tmp_path: Path) -> None:
    intent = tmp_path / "intent.json"
    status = tmp_path / "status.json"
    status.write_text(json.dumps(_status_payload(ready=True)))
    harness = ops.testing.Harness(charm.LVMSANCharm, meta=META, actions=ACTIONS)
    with patch("charm.subprocess.run") as run_mock:
        run_mock.return_value = subprocess.CompletedProcess([], 0, "", "")
        harness.begin()
        harness.update_config(
            {
                "vips": "192.0.2.10",
                "backend-key": "lvm-san.default",
                "intent-path": str(intent),
                "status-path": str(status),
            }
        )

    class _Event:
        def __init__(self) -> None:
            self.params = {"origin-lv": "vol-a", "snapshot-name": "snap-a"}
            self.failed: str | None = None
            self.results: dict = {}

        def fail(self, message: str) -> None:
            self.failed = message

        def set_results(self, results: dict) -> None:
            self.results = results

    event = _Event()
    with patch.object(
        harness.charm,
        "_run_command",
        side_effect=[
            subprocess.CompletedProcess([], 0, " thin\n", ""),
        ],
    ):
        harness.charm._on_lvm_snapshot_revert_action(event)  # type: ignore[arg-type]

    assert event.failed == "snapshot revert is not supported for thin LVM snapshots"
    harness.cleanup()
