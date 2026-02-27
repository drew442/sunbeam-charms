#!/usr/bin/env python3

# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Cinder LVM-SAN operator charm."""

import logging
from collections.abc import Callable, Mapping

import charms.lvm_san.v0.lvm_san_backend as lvm_san_backend
import ops
import ops_sunbeam.charm as charm
import ops_sunbeam.guard as sunbeam_guard
import ops_sunbeam.relation_handlers as relation_handlers
import ops_sunbeam.tracing as sunbeam_tracing

logger = logging.getLogger(__name__)


@sunbeam_tracing.trace_type
class LvmSanBackendRequiresHandler(relation_handlers.RelationHandler):
    """Handler for lvm-san-backend relation."""

    interface: lvm_san_backend.LvmSanBackendRequires

    def __init__(
        self,
        charm: charm.OSBaseOperatorCharm,
        relation_name: str,
        callback_f: Callable,
        mandatory: bool = True,
    ):
        super().__init__(charm, relation_name, callback_f, mandatory=mandatory)

    def setup_event_handler(self):
        """Configure event handlers for lvm-san-backend relation."""
        logger.debug("Setting up lvm-san-backend event handler")
        backend = sunbeam_tracing.trace_type(
            lvm_san_backend.LvmSanBackendRequires
        )(
            self.charm,
            self.relation_name,
        )
        self.framework.observe(backend.on.changed, self._on_backend_changed)
        self.framework.observe(backend.on.ready, self._on_backend_ready)
        self.framework.observe(backend.on.goneaway, self._on_backend_goneaway)
        return backend

    def _on_backend_changed(self, event: ops.framework.EventBase) -> None:
        self.callback_f(event)

    def _on_backend_ready(self, event: ops.framework.EventBase) -> None:
        self.callback_f(event)

    def _on_backend_goneaway(self, event: ops.framework.EventBase) -> None:
        self.callback_f(event)

    @property
    def ready(self) -> bool:
        """Whether lvm-san has published ready backend data."""
        return self.interface.ready


@sunbeam_tracing.trace_sunbeam_charm
class CinderVolumeLVMSANOperatorCharm(charm.OSCinderVolumeDriverOperatorCharm):
    """Cinder/LVM-SAN Operator charm."""

    service_name = "cinder-volume-lvm-san"

    @property
    def backend_key(self) -> str:
        """Return stable backend stanza key."""
        return "lvm-san." + self.model.app.name

    def get_relation_handlers(
        self, handlers: list[relation_handlers.RelationHandler] | None = None
    ) -> list[relation_handlers.RelationHandler]:
        """Relation handlers for this charm."""
        handlers = handlers or []
        self.lvm_san_backend = LvmSanBackendRequiresHandler(
            self,
            "lvm-san-backend",
            self.configure_charm,
            mandatory="lvm-san-backend" in self.mandatory_relations,
        )
        handlers.append(self.lvm_san_backend)
        return super().get_relation_handlers(handlers)

    def get_backend_configuration(self) -> Mapping[str, str | None]:
        """Return backend configuration rendered from lvm-san relation data."""
        backend_data = self.lvm_san_backend.interface.backend_data
        if not backend_data:
            raise sunbeam_guard.WaitingExceptionError(
                "Waiting for lvm-san backend data"
            )
        if not backend_data.ready:
            raise sunbeam_guard.WaitingExceptionError(backend_data.status)
        if backend_data.backend_key != self.backend_key:
            raise sunbeam_guard.WaitingExceptionError(
                "Unexpected backend-key from lvm-san relation"
            )
        if not backend_data.volume_group:
            raise sunbeam_guard.WaitingExceptionError(
                "Missing volume-group in lvm-san relation data"
            )

        iscsi_ip_address = (
            backend_data.portals[0] if backend_data.portals else backend_data.vips[0]
        )

        config: dict[str, str | None] = {
            "volume-driver": "cinder.volume.drivers.lvm.LVMVolumeDriver",
            "volume-group": backend_data.volume_group,
            "target-protocol": backend_data.target_protocol,
            "target-helper": backend_data.target_helper
            or self.model.config.get("target-helper"),
            "iscsi-ip-address": iscsi_ip_address,
            "volume-backend-name": backend_data.backend_key,
            "backend-availability-zone": self.model.config.get(
                "backend-availability-zone"
            ),
        }

        # Keep thin-provisioning deterministic from relation input.
        if backend_data.thin_pool:
            config["lvm-type"] = "thin"
            config["lvm-pool-name"] = backend_data.thin_pool

        return config


if __name__ == "__main__":  # pragma: nocover
    ops.main(CinderVolumeLVMSANOperatorCharm)
