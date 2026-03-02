#!/usr/bin/env python3

"""lvm-san machine charm (snap-first M3 scaffold)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
import shlex
import subprocess

import ops

from charms.lvm_san.v0.lvm_san_backend import LvmSanBackendData, LvmSanBackendProvides

logger = logging.getLogger(__name__)


class LVMSANCharm(ops.CharmBase):
    """Charm that bridges Juju intent to snap and publishes backend status."""

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)
        self.backend = LvmSanBackendProvides(self, "lvm-san-backend")

        self.framework.observe(self.on.install, self._reconcile)
        self.framework.observe(self.on.config_changed, self._reconcile)
        self.framework.observe(self.on.update_status, self._reconcile)
        self.framework.observe(
            self.on["lvm-san-backend"].relation_joined,
            self._reconcile,
        )

    def _reconcile(self, _event: ops.EventBase) -> None:
        try:
            self._write_intent()
            self._run_snap_reconcile()
            status_payload = self._load_status()
            backend_data = self._backend_data_from_status(status_payload)
            self.backend.set_backend_data(backend_data)
            if backend_data.ready:
                self.unit.status = ops.ActiveStatus(backend_data.status)
            else:
                self.unit.status = ops.BlockedStatus(backend_data.status)
        except Exception as exc:  # noqa: BLE001
            logger.exception("reconcile failed")
            self.unit.status = ops.BlockedStatus(f"snap reconcile failed: {exc}")

    def _write_intent(self) -> None:
        intent_path = Path(self.config["intent-path"])
        intent_path.parent.mkdir(parents=True, exist_ok=True)

        intent = {
            "api_version": "v1alpha1",
            "node_name": f"{self.app.name}/{self.unit.name.split('/')[-1]}",
            "fencing_required": True,
            "enforce_mode": bool(self.config["enforce-mode"]),
            "manage_pacemaker_vips": bool(self.config["manage-pacemaker-vips"]),
            "backends": [
                {
                    "backend_key": self.config["backend-key"],
                    "vips": self._csv_list(self.config["vips"]),
                    "portals": self._csv_list(self.config["portals"]),
                    "iscsi_targets": self._csv_list(self.config["iscsi-targets"]),
                    "block_devices": self._csv_list(self.config["block-devices"]),
                    "volume_group": self.config["volume-group"],
                    "thin_pool": self.config["thin-pool"] or None,
                    "target_helper": self.config["target-helper"],
                    "target_protocol": "iscsi",
                }
            ],
        }
        intent_path.write_text(json.dumps(intent, indent=2, sort_keys=True) + "\n")

    def _run_snap_reconcile(self) -> None:
        status_path = self.config["status-path"]
        intent_path = self.config["intent-path"]

        command = shlex.split(self.config["snap-reconcile-command"])
        if not command:
            raise ValueError("snap-reconcile-command must not be empty")
        command.extend(["--intent", intent_path, "--status", status_path])
        subprocess.run(command, check=True, capture_output=True, text=True)

    def _load_status(self) -> dict:
        return json.loads(Path(self.config["status-path"]).read_text())

    def _backend_data_from_status(self, status: dict) -> LvmSanBackendData:
        backends = status.get("backends") or []
        backend_key = self.config["backend-key"]
        backend = next(
            (entry for entry in backends if entry.get("backend_key") == backend_key),
            None,
        )
        if backend is None:
            raise ValueError(f"backend {backend_key} missing from status")

        cluster_ready = bool(status.get("cluster_ready"))
        fencing_configured = bool(status.get("fencing_configured"))
        backend_ready = bool(backend.get("ready"))
        ready = cluster_ready and fencing_configured and backend_ready

        status_msg = backend.get("status") or "status unavailable"
        if not fencing_configured:
            status_msg = "fencing not configured"
        elif not cluster_ready and backend_ready:
            status_msg = "cluster not ready"

        vips = self._csv_list(self.config["vips"])
        if not vips:
            raise ValueError("vips config must include at least one IP")
        # Portal selection policy for relation publication:
        # 1) snap status backend portals, 2) charm config portals, 3) backend VIPs.
        status_portals = self._list_from_status(backend.get("portals"))
        config_portals = self._csv_list(self.config["portals"])
        portals = status_portals or config_portals or vips

        return LvmSanBackendData(
            backend_key=backend_key,
            vips=tuple(vips),
            portals=tuple(portals),
            ready=ready,
            status=status_msg,
            volume_group=self.config["volume-group"],
            thin_pool=(self.config["thin-pool"] or None),
            target_helper=self.config["target-helper"],
            target_protocol="iscsi",
            auth_type="none",
            preferred_active_unit=backend.get("preferred_active_unit"),
            active_unit=backend.get("active_unit"),
        )

    @staticmethod
    def _csv_list(value: str | None) -> list[str]:
        if not value:
            return []
        return [item.strip() for item in value.split(",") if item.strip()]

    @staticmethod
    def _list_from_status(value: object) -> list[str]:
        if not value:
            return []
        if not isinstance(value, list):
            raise ValueError("status portals must be a list")
        portals: list[str] = []
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise ValueError("status portals entries must be non-empty strings")
            portals.append(item)
        return portals


if __name__ == "__main__":
    ops.main(LVMSANCharm)
