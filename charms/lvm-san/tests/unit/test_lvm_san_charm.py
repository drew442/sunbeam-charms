"""Unit tests for lvm-san snap-bridge charm behavior."""

from __future__ import annotations

import json
from pathlib import Path
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

    harness = ops.testing.Harness(charm.LVMSANCharm, meta=META)
    relation_id = harness.add_relation("lvm-san-backend", "cinder-volume-lvm-san")
    harness.add_relation_unit(relation_id, "cinder-volume-lvm-san/0")

    with patch("charm.subprocess.run"):
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

    harness = ops.testing.Harness(charm.LVMSANCharm, meta=META)
    relation_id = harness.add_relation("lvm-san-backend", "cinder-volume-lvm-san")
    harness.add_relation_unit(relation_id, "cinder-volume-lvm-san/0")

    with patch("charm.subprocess.run"):
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

    harness = ops.testing.Harness(charm.LVMSANCharm, meta=META)
    relation_id = harness.add_relation("lvm-san-backend", "cinder-volume-lvm-san")
    harness.add_relation_unit(relation_id, "cinder-volume-lvm-san/0")

    with patch("charm.subprocess.run"):
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
