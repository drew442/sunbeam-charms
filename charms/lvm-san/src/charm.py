#!/usr/bin/env python3

"""lvm-san machine charm (snap-first M3 scaffold)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
import re
import shlex
import socket
import subprocess
import time

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
        self.framework.observe(self.on.reconcile_now_action, self._on_reconcile_now_action)
        self.framework.observe(self.on.show_snap_status_action, self._on_show_snap_status_action)
        self.framework.observe(self.on.san_check_action, self._on_san_check_action)
        self.framework.observe(self.on.iscsi_login_action, self._on_iscsi_login_action)
        self.framework.observe(
            self.on.multipath_report_action, self._on_multipath_report_action
        )
        self.framework.observe(self.on.pcs_status_action, self._on_pcs_status_action)
        self.framework.observe(self.on.backend_move_action, self._on_backend_move_action)
        self.framework.observe(
            self.on.backend_clear_move_action, self._on_backend_clear_move_action
        )
        self.framework.observe(self.on.install_snap_action, self._on_install_snap_action)
        self.framework.observe(
            self.on.install_prereqs_action, self._on_install_prereqs_action
        )
        self.framework.observe(
            self.on.configure_iscsi_initiator_action,
            self._on_configure_iscsi_initiator_action,
        )
        self.framework.observe(
            self.on.apply_multipath_config_action, self._on_apply_multipath_config_action
        )
        self.framework.observe(self.on.bootstrap_cluster_action, self._on_bootstrap_cluster_action)
        self.framework.observe(
            self.on.set_node_hostname_action, self._on_set_node_hostname_action
        )
        self.framework.observe(
            self.on.restart_cluster_services_action,
            self._on_restart_cluster_services_action,
        )
        self.framework.observe(
            self.on.test_export_create_action, self._on_test_export_create_action
        )
        self.framework.observe(
            self.on.test_export_delete_action, self._on_test_export_delete_action
        )
        self.framework.observe(
            self.on.test_export_show_action, self._on_test_export_show_action
        )
        self.framework.observe(self.on.lvm_lv_create_action, self._on_lvm_lv_create_action)
        self.framework.observe(self.on.lvm_lv_remove_action, self._on_lvm_lv_remove_action)
        self.framework.observe(self.on.lvm_lv_resize_action, self._on_lvm_lv_resize_action)
        self.framework.observe(
            self.on.lvm_lv_activate_action, self._on_lvm_lv_activate_action
        )
        self.framework.observe(
            self.on.lvm_lv_deactivate_action, self._on_lvm_lv_deactivate_action
        )
        self.framework.observe(self.on.lvm_lv_rename_action, self._on_lvm_lv_rename_action)
        self.framework.observe(
            self.on.lvm_snapshot_create_action, self._on_lvm_snapshot_create_action
        )
        self.framework.observe(
            self.on.lvm_snapshot_delete_action, self._on_lvm_snapshot_delete_action
        )
        self.framework.observe(
            self.on.lvm_snapshot_revert_action, self._on_lvm_snapshot_revert_action
        )
        self.framework.observe(self.on.lvm_lv_show_action, self._on_lvm_lv_show_action)

    def _reconcile(self, _event: ops.EventBase) -> None:
        try:
            backend_data, _status_payload = self._reconcile_once()
            if backend_data.ready:
                self.unit.status = ops.ActiveStatus(backend_data.status)
            else:
                self.unit.status = ops.BlockedStatus(backend_data.status)
        except Exception as exc:  # noqa: BLE001
            logger.exception("reconcile failed")
            self.unit.status = ops.BlockedStatus(f"snap reconcile failed: {exc}")

    def _reconcile_once(self) -> tuple[LvmSanBackendData, dict]:
        self._write_intent()
        self._run_snap_reconcile()
        status_payload = self._load_status()
        backend_data = self._backend_data_from_status(status_payload)
        self.backend.set_backend_data(backend_data)
        return backend_data, status_payload

    def _on_reconcile_now_action(self, event: ops.ActionEvent) -> None:
        try:
            backend_data, status_payload = self._reconcile_once()
            event.set_results({
                "ready": backend_data.ready,
                "status": backend_data.status,
                "backend-key": backend_data.backend_key,
                "active-unit": backend_data.active_unit or "",
                "cluster-ready": bool(status_payload.get("cluster_ready")),
                "fencing-configured": bool(status_payload.get("fencing_configured")),
            })
        except Exception as exc:  # noqa: BLE001
            event.fail(str(exc))

    def _on_show_snap_status_action(self, event: ops.ActionEvent) -> None:
        try:
            status_payload = self._load_status()
            event.set_results({"status-json": json.dumps(status_payload, sort_keys=True)})
        except Exception as exc:  # noqa: BLE001
            event.fail(str(exc))

    def _on_san_check_action(self, event: ops.ActionEvent) -> None:
        timeout = str(int(event.params.get("timeout", 3)))
        portals = self._csv_list(event.params.get("portals")) or self._csv_list(
            self.config["iscsi-targets"]
        )
        if not portals:
            event.fail("no portals provided (action portals or config iscsi-targets)")
            return

        ok: list[str] = []
        failed: list[str] = []
        for portal in portals:
            host, port = self._parse_target_portal(portal)
            result = self._run_command(
                ["nc", "-zv", "-w", timeout, host, port],
                check=False,
            )
            if result.returncode == 0:
                ok.append(f"{host}:{port}")
            else:
                failed.append(f"{host}:{port}")
        event.set_results({
            "checked": ",".join(portals),
            "ok": ",".join(ok),
            "failed": ",".join(failed),
            "all-ok": not failed,
        })
        if failed:
            event.fail("connectivity check failed for one or more portals")

    def _on_iscsi_login_action(self, event: ops.ActionEvent) -> None:
        targets = self._csv_list(event.params.get("targets")) or self._csv_list(
            self.config["iscsi-targets"]
        )
        if not targets:
            event.fail("no targets provided (action targets or config iscsi-targets)")
            return

        failures: list[str] = []
        details: list[str] = []
        for target in targets:
            portal = target.split(",", 1)[0].strip()
            discover = self._run_command(
                [
                    "iscsiadm",
                    "-m",
                    "discoverydb",
                    "-t",
                    "sendtargets",
                    "-p",
                    portal,
                    "--discover",
                ],
                check=False,
            )
            if discover.returncode != 0:
                failures.append(f"{portal}:discover")
                details.append(
                    f"{portal}:discover:rc={discover.returncode}:"
                    f"{(discover.stderr or discover.stdout).strip()}"
                )
                continue
            login = self._run_command(
                ["iscsiadm", "-m", "node", "-p", portal, "--login"],
                check=False,
            )
            login_msg = (login.stderr or login.stdout).strip()
            if login.returncode != 0 and not self._is_nonfatal_iscsi_login_error(login_msg):
                failures.append(f"{portal}:login")
                details.append(
                    f"{portal}:login:rc={login.returncode}:"
                    f"{login_msg}"
                )
            else:
                details.append(f"{portal}:login:rc={login.returncode}:ok")
        event.set_results({
            "targets": ",".join(targets),
            "failed": ",".join(failures),
            "all-ok": not failures,
            "details": " | ".join(details),
        })
        if failures:
            event.fail("iscsi discovery/login failed for one or more targets")

    def _on_multipath_report_action(self, event: ops.ActionEvent) -> None:
        wwid = (event.params.get("wwid") or "").strip()
        result = self._run_command(["multipath", "-ll"], check=False)
        if result.returncode != 0:
            event.fail("multipath -ll failed")
            return
        report = result.stdout.strip()
        event.set_results({
            "contains-wwid": (wwid.lower() in report.lower()) if wwid else False,
            "wwid": wwid,
            "report": report,
        })

    def _on_pcs_status_action(self, event: ops.ActionEvent) -> None:
        result = self._run_command(["pcs", "status", "--full"], check=False)
        if result.returncode != 0:
            event.fail("pcs status --full failed")
            return
        event.set_results({"pcs-status": result.stdout.strip()})

    def _on_backend_move_action(self, event: ops.ActionEvent) -> None:
        backend_key = (event.params.get("backend-key") or self.config["backend-key"]).strip()
        target_unit = (event.params.get("target-unit") or "").strip()
        if not target_unit:
            event.fail("target-unit is required (example: lvm-san/1)")
            return
        target_node = self._resolve_target_node(target_unit)
        if not target_node:
            event.fail(
                f"unable to resolve target-unit {target_unit} to a pacemaker node name"
            )
            return
        group_name = f"grp-{self._sanitize_resource_name(backend_key)}"
        result = self._run_command(
            ["pcs", "resource", "move", group_name, target_node], check=False
        )
        if result.returncode != 0:
            message = (result.stderr or result.stdout or "").strip()
            if "Requested item already exists" in message:
                event.set_results(
                    {
                        "group": group_name,
                        "target-unit": target_unit,
                        "target-node": target_node,
                        "moved": False,
                        "already-on-target": True,
                    }
                )
                return
            event.fail(
                f"failed to move resource group {group_name} to {target_node}: "
                f"{message}"
            )
            return
        event.set_results(
            {
                "group": group_name,
                "target-unit": target_unit,
                "target-node": target_node,
                "moved": True,
                "already-on-target": False,
            }
        )

    def _on_backend_clear_move_action(self, event: ops.ActionEvent) -> None:
        backend_key = (event.params.get("backend-key") or self.config["backend-key"]).strip()
        group_name = f"grp-{self._sanitize_resource_name(backend_key)}"
        result = self._run_command(
            ["pcs", "resource", "clear", group_name], check=False
        )
        if result.returncode != 0:
            event.fail(f"failed to clear resource move constraints for {group_name}")
            return
        event.set_results({"group": group_name, "cleared": True})

    def _on_install_snap_action(self, event: ops.ActionEvent) -> None:
        file_path = (event.params.get("file") or "").strip()
        channel = (event.params.get("channel") or "").strip()
        dangerous = bool(event.params.get("dangerous", True))
        classic = bool(event.params.get("classic", False))

        if file_path:
            cmd = ["snap", "install", file_path]
            if dangerous:
                cmd.append("--dangerous")
            if classic:
                cmd.append("--classic")
            result = self._run_command(cmd, check=False)
            if (
                result.returncode != 0
                and "already installed" in (result.stderr or "").lower()
            ):
                refresh_cmd = ["snap", "refresh", "--amend", file_path]
                if classic:
                    refresh_cmd.append("--classic")
                result = self._run_command(refresh_cmd, check=False)
        else:
            cmd = ["snap", "install", "lvm-san-agent"]
            if channel:
                cmd.extend(["--channel", channel])
            if classic:
                cmd.append("--classic")
            result = self._run_command(cmd, check=False)
            if (
                result.returncode != 0
                and "already installed" in (result.stderr or "").lower()
            ):
                refresh_cmd = ["snap", "refresh", "lvm-san-agent"]
                if channel:
                    refresh_cmd.extend(["--channel", channel])
                result = self._run_command(refresh_cmd, check=False)

        if result.returncode != 0:
            event.fail(f"snap install failed: {result.stderr.strip()}")
            return

        event.set_results({
            "installed": True,
            "file": file_path,
            "channel": channel,
            "dangerous": dangerous,
            "classic": classic,
        })

    def _on_install_prereqs_action(self, event: ops.ActionEvent) -> None:
        packages = (
            event.params.get("packages")
            or "open-iscsi,multipath-tools,netcat-openbsd,pacemaker,corosync,pcs,resource-agents-base,jq,targetcli-fb"
        )
        package_list = [p.strip() for p in str(packages).split(",") if p.strip()]
        if not package_list:
            event.fail("no packages provided")
            return

        update = self._run_command(["apt-get", "update"], check=False)
        if update.returncode != 0:
            event.fail(f"apt-get update failed: {update.stderr.strip()}")
            return

        install = self._run_command(
            ["apt-get", "install", "-y", *package_list],
            check=False,
        )
        if install.returncode != 0:
            event.fail(f"apt-get install failed: {install.stderr.strip()}")
            return

        event.set_results({"installed-packages": ",".join(package_list)})

    def _on_configure_iscsi_initiator_action(self, event: ops.ActionEvent) -> None:
        iqn = (event.params.get("iqn") or "").strip()
        if not iqn:
            event.fail("iqn parameter is required")
            return

        Path("/etc/iscsi/initiatorname.iscsi").write_text(f"InitiatorName={iqn}\n")
        logout = self._run_command(
            ["iscsiadm", "-m", "node", "-U", "all"], check=False
        )
        logout_msg = (logout.stderr or logout.stdout).strip().lower()
        if logout.returncode != 0 and "no matching sessions" not in logout_msg:
            event.fail("failed to logout existing iSCSI sessions before IQN apply")
            return

        restart = self._run_command(
            ["systemctl", "restart", "iscsid", "open-iscsi"], check=False
        )
        if restart.returncode != 0:
            restart = self._run_command(["systemctl", "restart", "iscsid"], check=False)
        if restart.returncode != 0:
            event.fail("failed to restart iSCSI services")
            return
        event.set_results({
            "iqn": iqn,
            "sessions-logged-out": logout.returncode == 0,
            "iscsid-restarted": True,
        })

    def _on_apply_multipath_config_action(self, event: ops.ActionEvent) -> None:
        source = (event.params.get("source") or "").strip()
        if not source:
            event.fail("source parameter is required")
            return
        src_path = Path(source)
        if not src_path.exists():
            event.fail(f"source file does not exist: {source}")
            return

        target = Path("/etc/multipath.conf")
        target.write_text(src_path.read_text())
        restart = self._run_command(["systemctl", "restart", "multipathd"], check=False)
        if restart.returncode != 0:
            event.fail("failed to restart multipathd")
            return
        event.set_results({"source": source, "target": str(target), "multipathd-restarted": True})

    def _on_bootstrap_cluster_action(self, event: ops.ActionEvent) -> None:
        nodes_raw = (event.params.get("nodes") or "").strip()
        if not nodes_raw:
            event.fail("nodes parameter is required (comma-separated hostnames or IPs)")
            return
        nodes = [n.strip() for n in nodes_raw.split(",") if n.strip()]
        if len(nodes) < 2:
            event.fail("nodes must include at least two entries")
            return

        cluster_name = (event.params.get("cluster-name") or "lvm-san-cluster").strip()
        password = (event.params.get("hacluster-password") or "hacluster").strip()

        self._run_command(
            ["bash", "-lc", f"echo hacluster:{shlex.quote(password)} | chpasswd"],
            check=False,
        )
        pcsd = self._run_command(["systemctl", "enable", "--now", "pcsd"], check=False)
        if pcsd.returncode != 0:
            event.fail("failed to enable/start pcsd")
            return

        auth = self._run_command(
            ["pcs", "host", "auth", *nodes, "-u", "hacluster", "-p", password],
            check=False,
        )
        if auth.returncode != 0:
            event.fail("pcs host auth failed")
            return

        setup = self._run_command(
            ["pcs", "cluster", "setup", cluster_name, *nodes, "--force"],
            check=False,
        )
        if setup.returncode != 0:
            event.fail("pcs cluster setup failed")
            return

        start = self._run_command(["pcs", "cluster", "start", "--all"], check=False)
        if start.returncode != 0:
            event.fail("pcs cluster start --all failed")
            return

        self._run_command(["pcs", "cluster", "enable", "--all"], check=False)
        self._run_command(["pcs", "property", "set", "stonith-enabled=false"], check=False)
        self._run_command(["pcs", "property", "set", "no-quorum-policy=ignore"], check=False)
        online_nodes = self._wait_for_online_nodes(expected_count=2, timeout=90, interval=3)

        event.set_results({
            "cluster-name": cluster_name,
            "nodes": ",".join(nodes),
            "bootstrapped": True,
            "online-nodes": ",".join(online_nodes),
        })
        if len(online_nodes) < 2:
            event.fail(
                "cluster bootstrapped but less than two nodes are online; "
                "check pcs/corosync status"
            )

    def _on_set_node_hostname_action(self, event: ops.ActionEvent) -> None:
        hostname = (event.params.get("hostname") or "").strip()
        if not hostname:
            event.fail("hostname parameter is required")
            return
        result = self._run_command(["hostnamectl", "set-hostname", hostname], check=False)
        if result.returncode != 0:
            event.fail("failed to set hostname")
            return
        event.set_results({"hostname": hostname, "updated": True})

    def _on_restart_cluster_services_action(self, event: ops.ActionEvent) -> None:
        self._run_command(["systemctl", "enable", "--now", "pcsd"], check=False)
        self._run_command(["systemctl", "restart", "corosync"], check=False)
        self._run_command(["systemctl", "restart", "pacemaker"], check=False)
        event.set_results({"restarted": "corosync,pacemaker,pcsd"})

    def _on_test_export_create_action(self, event: ops.ActionEvent) -> None:
        if not bool(self.config["test-export-enabled"]):
            event.fail("set config test-export-enabled=true before creating test export")
            return
        try:
            backend_data, _status_payload = self._reconcile_once()
            event.set_results({
                "backend-key": backend_data.backend_key,
                "status": backend_data.status,
                "ready": backend_data.ready,
                "test-export-iqn": self._test_export_iqn(),
                "test-export-lv": self.config["test-export-lv-name"],
                "test-export-size": self.config["test-export-lv-size"],
            })
            if not backend_data.ready:
                event.fail(f"backend not ready after export create: {backend_data.status}")
        except Exception as exc:  # noqa: BLE001
            event.fail(str(exc))

    def _on_test_export_delete_action(self, event: ops.ActionEvent) -> None:
        key = self._sanitize_resource_name(str(self.config["backend-key"]))
        iqn = self._test_export_iqn()
        lv_name = str(self.config["test-export-lv-name"])
        vg_name = str(self.config["volume-group"])
        target_resource = f"iscsitgt-{key}"
        group_name = f"grp-{key}"

        self._run_command(["targetcli", "clearconfig", "confirm=True"], check=False)
        self._run_command(["targetcli", "saveconfig"], check=False)
        self._run_command(["pcs", "resource", "group", "remove", group_name, target_resource], check=False)
        self._run_command(["pcs", "resource", "delete", target_resource], check=False)
        self._run_command(["pcs", "resource", "cleanup", target_resource], check=False)
        self._run_command(["lvremove", "-y", f"{vg_name}/{lv_name}"], check=False)

        event.set_results({
            "backend-key": str(self.config["backend-key"]),
            "deleted": True,
            "test-export-iqn": iqn,
            "test-export-lv": f"{vg_name}/{lv_name}",
        })

    def _on_test_export_show_action(self, event: ops.ActionEvent) -> None:
        iqn = self._test_export_iqn()
        lv_name = str(self.config["test-export-lv-name"])
        vg_name = str(self.config["volume-group"])
        key = self._sanitize_resource_name(str(self.config["backend-key"]))
        target_resource = f"iscsitgt-{key}"

        lvs = self._run_command(
            ["lvs", "--noheadings", "-o", "lv_name", f"{vg_name}/{lv_name}"],
            check=False,
        )
        target = self._run_command(["targetcli", f"/iscsi/{iqn}", "ls"], check=False)
        pcs = self._run_command(["pcs", "resource", "show", target_resource], check=False)
        event.set_results({
            "test-export-iqn": iqn,
            "lv-exists": lvs.returncode == 0,
            "target-exists": target.returncode == 0,
            "pacemaker-resource-exists": pcs.returncode == 0,
            "targetcli-output": (target.stdout or target.stderr).strip(),
        })

    def _on_lvm_lv_create_action(self, event: ops.ActionEvent) -> None:
        if not self._require_active_backend_unit(event):
            return
        lv_name = (event.params.get("lv-name") or "").strip()
        size = (event.params.get("size") or "").strip()
        activate = bool(event.params.get("activate", True))
        if not lv_name or not size:
            event.fail("lv-name and size are required")
            return
        vg_name = str(self.config["volume-group"])
        create = self._run_command(["lvcreate", "-L", size, "-n", lv_name, vg_name], check=False)
        if create.returncode != 0:
            event.fail(create.stderr.strip() or create.stdout.strip() or "lvcreate failed")
            return
        results = {"vg": vg_name, "lv-name": lv_name, "size": size, "created": True}
        if activate:
            activate_result = self._run_command(
                ["lvchange", "-ay", self._full_lv_path(vg_name, lv_name)], check=False
            )
            if activate_result.returncode != 0:
                event.fail(
                    activate_result.stderr.strip()
                    or activate_result.stdout.strip()
                    or "lvchange -ay failed"
                )
                return
            results["activated"] = True
        event.set_results(results)

    def _on_lvm_lv_remove_action(self, event: ops.ActionEvent) -> None:
        if not self._require_active_backend_unit(event):
            return
        lv_name = (event.params.get("lv-name") or "").strip()
        if not lv_name:
            event.fail("lv-name is required")
            return
        vg_name = str(self.config["volume-group"])
        result = self._run_command(
            ["lvremove", "-y", self._full_lv_path(vg_name, lv_name)], check=False
        )
        if result.returncode != 0:
            event.fail(result.stderr.strip() or result.stdout.strip() or "lvremove failed")
            return
        event.set_results({"vg": vg_name, "lv-name": lv_name, "removed": True})

    def _on_lvm_lv_resize_action(self, event: ops.ActionEvent) -> None:
        if not self._require_active_backend_unit(event):
            return
        lv_name = (event.params.get("lv-name") or "").strip()
        size = (event.params.get("size") or "").strip()
        if not lv_name or not size:
            event.fail("lv-name and size are required")
            return
        vg_name = str(self.config["volume-group"])
        result = self._run_command(
            ["lvextend", "-L", size, self._full_lv_path(vg_name, lv_name)], check=False
        )
        if result.returncode != 0:
            event.fail(result.stderr.strip() or result.stdout.strip() or "lvextend failed")
            return
        event.set_results({"vg": vg_name, "lv-name": lv_name, "size": size, "resized": True})

    def _on_lvm_lv_activate_action(self, event: ops.ActionEvent) -> None:
        self._set_lv_activation(event, activate=True)

    def _on_lvm_lv_deactivate_action(self, event: ops.ActionEvent) -> None:
        self._set_lv_activation(event, activate=False)

    def _set_lv_activation(self, event: ops.ActionEvent, *, activate: bool) -> None:
        if not self._require_active_backend_unit(event):
            return
        lv_name = (event.params.get("lv-name") or "").strip()
        if not lv_name:
            event.fail("lv-name is required")
            return
        vg_name = str(self.config["volume-group"])
        cmd = ["lvchange", "-ay" if activate else "-an", self._full_lv_path(vg_name, lv_name)]
        result = self._run_command(cmd, check=False)
        if result.returncode != 0:
            event.fail(result.stderr.strip() or result.stdout.strip() or "lvchange failed")
            return
        event.set_results(
            {"vg": vg_name, "lv-name": lv_name, "activated": activate, "changed": True}
        )

    def _on_lvm_lv_rename_action(self, event: ops.ActionEvent) -> None:
        if not self._require_active_backend_unit(event):
            return
        old_name = (event.params.get("old-name") or "").strip()
        new_name = (event.params.get("new-name") or "").strip()
        if not old_name or not new_name:
            event.fail("old-name and new-name are required")
            return
        vg_name = str(self.config["volume-group"])
        result = self._run_command(["lvrename", vg_name, old_name, new_name], check=False)
        if result.returncode != 0:
            event.fail(result.stderr.strip() or result.stdout.strip() or "lvrename failed")
            return
        event.set_results({"vg": vg_name, "old-name": old_name, "new-name": new_name, "renamed": True})

    def _on_lvm_snapshot_create_action(self, event: ops.ActionEvent) -> None:
        if not self._require_active_backend_unit(event):
            return
        origin_lv = (event.params.get("origin-lv") or "").strip()
        snapshot_name = (event.params.get("snapshot-name") or "").strip()
        snapshot_size = (event.params.get("size") or "").strip()
        if not origin_lv or not snapshot_name:
            event.fail("origin-lv and snapshot-name are required")
            return
        vg_name = str(self.config["volume-group"])
        cmd = ["lvcreate", "-s", "-n", snapshot_name]
        if snapshot_size:
            cmd.extend(["-L", snapshot_size])
        cmd.append(self._full_lv_path(vg_name, origin_lv))
        result = self._run_command(cmd, check=False)
        if result.returncode != 0:
            event.fail(result.stderr.strip() or result.stdout.strip() or "lvcreate snapshot failed")
            return
        event.set_results(
            {
                "vg": vg_name,
                "origin-lv": origin_lv,
                "snapshot-name": snapshot_name,
                "snapshot-size": snapshot_size,
                "created": True,
            }
        )

    def _on_lvm_snapshot_delete_action(self, event: ops.ActionEvent) -> None:
        if not self._require_active_backend_unit(event):
            return
        snapshot_name = (event.params.get("snapshot-name") or "").strip()
        if not snapshot_name:
            event.fail("snapshot-name is required")
            return
        vg_name = str(self.config["volume-group"])
        result = self._run_command(
            ["lvremove", "-y", self._full_lv_path(vg_name, snapshot_name)], check=False
        )
        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip() or "lvremove snapshot failed"
            if "Failed to find logical volume" in message:
                event.set_results(
                    {
                        "vg": vg_name,
                        "snapshot-name": snapshot_name,
                        "deleted": True,
                        "already-absent": True,
                    }
                )
                return
            event.fail(message)
            return
        event.set_results(
            {
                "vg": vg_name,
                "snapshot-name": snapshot_name,
                "deleted": True,
                "already-absent": False,
            }
        )

    def _on_lvm_snapshot_revert_action(self, event: ops.ActionEvent) -> None:
        if not self._require_active_backend_unit(event):
            return
        origin_lv = (event.params.get("origin-lv") or "").strip()
        snapshot_name = (event.params.get("snapshot-name") or "").strip()
        reactivate = bool(event.params.get("reactivate", True))
        recreate_snapshot = bool(event.params.get("recreate-snapshot", False))
        snapshot_size = (event.params.get("snapshot-size") or "").strip()
        if not origin_lv or not snapshot_name:
            event.fail("origin-lv and snapshot-name are required")
            return

        vg_name = str(self.config["volume-group"])
        snapshot_path = self._full_lv_path(vg_name, snapshot_name)
        segtype = self._run_command(
            ["lvs", "--noheadings", "-o", "segtype", snapshot_path], check=False
        )
        if segtype.returncode != 0:
            event.fail(segtype.stderr.strip() or segtype.stdout.strip() or "unable to read snapshot type")
            return
        if "thin" in segtype.stdout.lower():
            event.fail("snapshot revert is not supported for thin LVM snapshots")
            return

        origin_path = self._full_lv_path(vg_name, origin_lv)
        self._run_command(["lvchange", "-an", origin_path], check=False)
        merge = self._run_command(["lvconvert", "--merge", "-y", snapshot_path], check=False)
        if merge.returncode != 0:
            event.fail(merge.stderr.strip() or merge.stdout.strip() or "lvconvert --merge failed")
            return

        if reactivate:
            activate = self._run_command(["lvchange", "-ay", origin_path], check=False)
            if activate.returncode != 0:
                event.fail(activate.stderr.strip() or activate.stdout.strip() or "lvchange -ay failed")
                return

        recreated = False
        if recreate_snapshot:
            cmd = ["lvcreate", "-s", "-n", snapshot_name]
            if snapshot_size:
                cmd.extend(["-L", snapshot_size])
            cmd.append(origin_path)
            recreate = self._run_command(cmd, check=False)
            if recreate.returncode != 0:
                event.fail(
                    recreate.stderr.strip()
                    or recreate.stdout.strip()
                    or "failed to recreate snapshot after revert"
                )
                return
            recreated = True

        event.set_results(
            {
                "vg": vg_name,
                "origin-lv": origin_lv,
                "snapshot-name": snapshot_name,
                "reverted": True,
                "reactivated": reactivate,
                "snapshot-recreated": recreated,
            }
        )

    def _on_lvm_lv_show_action(self, event: ops.ActionEvent) -> None:
        vg_name = str(self.config["volume-group"])
        lv_name = (event.params.get("lv-name") or "").strip()
        cmd = [
            "lvs",
            "--noheadings",
            "--units",
            "b",
            "-o",
            "lv_name,lv_size,lv_attr,origin,segtype,data_percent,metadata_percent",
            vg_name,
        ]
        result = self._run_command(cmd, check=False)
        if result.returncode != 0:
            event.fail(result.stderr.strip() or result.stdout.strip() or "lvs failed")
            return
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if lv_name:
            lines = [line for line in lines if line.split()[0] == lv_name]
        event.set_results(
            {
                "vg": vg_name,
                "lv-name-filter": lv_name,
                "count": len(lines),
                "lvs-output": "\n".join(lines),
            }
        )

    def _write_intent(self) -> None:
        intent_path = Path(self.config["intent-path"])
        intent_path.parent.mkdir(parents=True, exist_ok=True)

        intent = {
            "api_version": "v1alpha1",
            "node_name": f"{self.app.name}/{self.unit.name.split('/')[-1]}",
            "fencing_required": bool(self.config["fencing-required"]),
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
                    "test_export_enabled": bool(self.config["test-export-enabled"]),
                    "test_export_lv_name": self.config["test-export-lv-name"],
                    "test_export_lv_size": self.config["test-export-lv-size"],
                    "test_export_iqn": self._test_export_iqn(),
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
        result = subprocess.run(command, check=False, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(
                "snap reconcile command failed "
                f"(rc={result.returncode}): stdout={result.stdout.strip()} "
                f"stderr={result.stderr.strip()}"
            )

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

        active_unit = backend.get("active_unit")
        active_node = self._active_backend_node_name(backend_key)
        if not active_node:
            active_node = self._resolve_target_node(active_unit or "")

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
            active_unit=active_unit,
            active_node=active_node,
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

    @staticmethod
    def _sanitize_resource_name(raw: str) -> str:
        return "".join(ch if ch.isalnum() else "-" for ch in raw).strip("-")

    @staticmethod
    def _parse_target_portal(target: str) -> tuple[str, str]:
        value = target.strip()
        if not value:
            raise ValueError("empty portal/target value")
        if "," in value:
            value = value.split(",", 1)[0].strip()
        if ":" in value:
            host, port = value.rsplit(":", 1)
            return host.strip(), port.strip()
        return value, "3260"

    def _resolve_target_node(self, target: str) -> str | None:
        if "/" not in target:
            return target
        try:
            unit_index = int(target.rsplit("/", 1)[1])
        except (IndexError, ValueError):
            return None
        status = self._run_command(["pcs", "status", "--full"], check=False)
        if status.returncode != 0:
            return None
        nodes = re.findall(r"^\s*\*\s*Node\s+(\S+)", status.stdout, flags=re.MULTILINE)
        if not nodes:
            return None
        local_node = self._local_cluster_node_name(nodes)
        try:
            local_unit_index = int(self.unit.name.rsplit("/", 1)[1])
        except (IndexError, ValueError):
            local_unit_index = None
        if local_node and local_unit_index is not None:
            if unit_index == local_unit_index:
                return local_node
            if len(nodes) == 2:
                for node in nodes:
                    if node != local_node:
                        return node
        suffix = f"-{unit_index}"
        for node in nodes:
            if node.endswith(suffix):
                return node
        return None

    def _active_backend_node_name(self, backend_key: str) -> str | None:
        group = f"grp-{self._sanitize_resource_name(backend_key)}"
        status = self._run_command(["pcs", "status", "--full"], check=False)
        if status.returncode != 0:
            return None

        in_group = False
        for line in status.stdout.splitlines():
            if f"Resource Group: {group}:" in line:
                in_group = True
                continue
            if in_group and line.lstrip().startswith("* Resource Group:"):
                break
            if not in_group:
                continue
            match = re.search(r":\s+Started\s+(\S+)\s*$", line)
            if match:
                return match.group(1)
        return None

    @staticmethod
    def _local_cluster_node_name(nodes: list[str]) -> str | None:
        local_names = {
            socket.gethostname(),
            socket.getfqdn(),
            socket.gethostname().split(".", 1)[0],
            socket.getfqdn().split(".", 1)[0],
        }
        for node in nodes:
            short = node.split(".", 1)[0]
            if node in local_names or short in local_names:
                return node
        return None

    @staticmethod
    def _pcs_online_nodes(status_output: str) -> list[str]:
        return re.findall(
            r"^\s*\*\s*Node\s+(\S+)\s+\(\d+\):\s+online",
            status_output,
            flags=re.MULTILINE | re.IGNORECASE,
        )

    def _wait_for_online_nodes(
        self, expected_count: int, timeout: int = 90, interval: int = 3
    ) -> list[str]:
        deadline = time.time() + max(timeout, 1)
        last_seen: list[str] = []
        while time.time() < deadline:
            status = self._run_command(["pcs", "status", "--full"], check=False)
            if status.returncode == 0:
                last_seen = self._pcs_online_nodes(status.stdout)
                if len(last_seen) >= expected_count:
                    return last_seen
            time.sleep(max(interval, 1))
        return last_seen

    @staticmethod
    def _is_nonfatal_iscsi_login_error(message: str) -> bool:
        text = (message or "").lower()
        already_logged_in_markers = (
            "already present",
            "already logged in",
            "1 session requested, but 1 already present",
        )
        if any(marker in text for marker in already_logged_in_markers):
            return True
        return False

    def _test_export_iqn(self) -> str:
        prefix = str(self.config["test-export-iqn-prefix"]).strip().rstrip(":")
        suffix = self._sanitize_resource_name(str(self.config["backend-key"]))
        return f"{prefix}:{suffix}"

    @staticmethod
    def _full_lv_path(vg_name: str, lv_name: str) -> str:
        if "/" in lv_name:
            return lv_name
        return f"{vg_name}/{lv_name}"

    def _require_active_backend_unit(self, event: ops.ActionEvent) -> bool:
        active = self._active_backend_unit_name()
        if not active:
            event.fail("unable to determine active backend unit from status")
            return False
        if active != self.unit.name:
            event.fail(
                f"run this action on active unit {active} (current unit: {self.unit.name})"
            )
            return False
        return True

    def _active_backend_unit_name(self) -> str:
        status = self._load_status()
        backend_key = str(self.config["backend-key"])
        for entry in status.get("backends", []):
            if entry.get("backend_key") == backend_key:
                active = entry.get("active_unit")
                return str(active) if active else ""
        return ""

    @staticmethod
    def _run_command(
        args: list[str], check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(args, capture_output=True, text=True, check=check)


if __name__ == "__main__":
    ops.main(LVMSANCharm)
