#!/usr/bin/env python3

#
# Copyright 2025 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

"""Cinder LVM Operator Charm.

This charm provides Cinder <-> LVM integration as part
of an OpenStack deployment.
"""

import logging
import typing

import ops
import ops_sunbeam.charm as charm
import ops_sunbeam.storage as sunbeam_storage
import ops_sunbeam.tracing as sunbeam_tracing

logger = logging.getLogger(__name__)


@sunbeam_tracing.trace_sunbeam_charm
class CinderVolumeLVMOperatorCharm(charm.OSCinderVolumeDriverOperatorCharm):
    """Cinder/LVM Operator charm."""

    service_name = "cinder-volume-lvm"

    @property
    def backend_key(self) -> str:
        """Return the backend key."""
        return "lvm." + self.model.app.name

    def _configuration_type_overrides(self) -> dict[str, typing.Any]:
        """Configuration type overrides for pydantic model generation."""
        return {
            "volume-group": typing.Annotated[str, sunbeam_storage.Required],
        }


if __name__ == "__main__":  # pragma: nocover
    ops.main(CinderVolumeLVMOperatorCharm)
