"""Library for the ``lvm-san-backend`` relation interface.

This interface is used by an ``lvm-san`` provider to publish Cinder backend
connection details to subordinate storage-backend charms.
"""

# The unique Charmhub library identifier, never change it
LIBID = "44bb79dfefeb4eef8412d5f4cfe73a46"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 4

import ipaddress
import json
import logging
from dataclasses import dataclass
from typing import Any, Mapping

from ops.charm import CharmBase, RelationBrokenEvent, RelationChangedEvent
from ops.framework import EventBase, EventSource, Handle, Object, ObjectEvents
from ops.model import ModelError, Relation

logger = logging.getLogger(__name__)

DEFAULT_RELATION_NAME = "lvm-san-backend"


class DataValidationError(RuntimeError):
    """Raised when relation data is invalid."""


@dataclass(frozen=True)
class LvmSanBackendData:
    """Typed representation of relation payload."""

    backend_key: str
    vips: tuple[str, ...]
    ready: bool
    status: str
    portals: tuple[str, ...] = ()
    volume_group: str | None = None
    thin_pool: str | None = None
    target_helper: str | None = None
    target_protocol: str = "iscsi"
    auth_type: str = "none"
    chap_username_secret_id: str | None = None
    chap_password_secret_id: str | None = None
    preferred_active_unit: str | None = None
    active_unit: str | None = None
    active_node: str | None = None

    def to_relation_data(self) -> dict[str, str]:
        """Dump this dataclass to relation databag-safe values."""
        payload: dict[str, Any] = {
            "backend-key": self.backend_key,
            "vips": list(self.vips),
            "portals": list(self.portals),
            "ready": self.ready,
            "status": self.status,
            "target-protocol": self.target_protocol,
            "auth-type": self.auth_type,
        }
        optional_fields = {
            "volume-group": self.volume_group,
            "thin-pool": self.thin_pool,
            "target-helper": self.target_helper,
            "chap-username-secret-id": self.chap_username_secret_id,
            "chap-password-secret-id": self.chap_password_secret_id,
            "preferred-active-unit": self.preferred_active_unit,
            "active-unit": self.active_unit,
            "active-node": self.active_node,
        }
        payload.update({k: v for k, v in optional_fields.items() if v is not None})
        _validate_payload(payload)
        return _dump_relation_data(payload)

    @classmethod
    def from_relation_data(cls, relation_data: Mapping[str, str]) -> "LvmSanBackendData":
        """Parse relation databag values into a typed payload."""
        payload = _load_relation_data(relation_data)
        _validate_payload(payload)
        return cls(
            backend_key=payload["backend-key"],
            vips=tuple(payload["vips"]),
            portals=tuple(payload.get("portals", [])),
            ready=payload["ready"],
            status=payload["status"],
            volume_group=payload.get("volume-group"),
            thin_pool=payload.get("thin-pool"),
            target_helper=payload.get("target-helper"),
            target_protocol=payload.get("target-protocol", "iscsi"),
            auth_type=payload.get("auth-type", "none"),
            chap_username_secret_id=payload.get("chap-username-secret-id"),
            chap_password_secret_id=payload.get("chap-password-secret-id"),
            preferred_active_unit=payload.get("preferred-active-unit"),
            active_unit=payload.get("active-unit"),
            active_node=payload.get("active-node"),
        )


class LvmSanBackendChangedEvent(EventBase):
    """Event emitted when provider backend data changes."""

    def __init__(self, handle: Handle, backend_data: dict[str, str]):
        super().__init__(handle)
        self.backend_data = backend_data

    def snapshot(self) -> dict[str, Any]:
        """Persist event state."""
        return {"backend_data": self.backend_data}

    def restore(self, snapshot: dict[str, Any]) -> None:
        """Restore event state."""
        super().restore(snapshot)
        self.backend_data = snapshot["backend_data"]


class LvmSanBackendReadyEvent(LvmSanBackendChangedEvent):
    """Event emitted when provider data is complete and marked ready."""


class LvmSanBackendGoneAwayEvent(EventBase):
    """Event emitted when relation is broken."""


class LvmSanBackendClientEvents(ObjectEvents):
    """Events exposed by ``LvmSanBackendRequires``."""

    changed = EventSource(LvmSanBackendChangedEvent)
    ready = EventSource(LvmSanBackendReadyEvent)
    goneaway = EventSource(LvmSanBackendGoneAwayEvent)


class LvmSanBackendHasClientsEvent(EventBase):
    """Event emitted when a new client joins the relation."""


class LvmSanBackendProviderEvents(ObjectEvents):
    """Events exposed by ``LvmSanBackendProvides``."""

    has_clients = EventSource(LvmSanBackendHasClientsEvent)


class LvmSanBackendRequires(Object):
    """Requirer side helper for the ``lvm-san-backend`` relation."""

    on = LvmSanBackendClientEvents()

    def __init__(
        self,
        charm: CharmBase,
        relation_name: str = DEFAULT_RELATION_NAME,
    ) -> None:
        super().__init__(charm, relation_name)
        self._charm = charm
        self._relation_name = relation_name

        events = self._charm.on[relation_name]
        self.framework.observe(events.relation_changed, self._on_relation_changed)
        self.framework.observe(events.relation_broken, self._on_relation_broken)

    @property
    def relation(self) -> Relation | None:
        """Return relation (relation limit is expected to be 1)."""
        return self.model.get_relation(self._relation_name)

    @property
    def backend_data(self) -> LvmSanBackendData | None:
        """Return parsed backend data from the provider app databag."""
        relation = self.relation
        if not relation or not relation.app:
            return None
        app_data = relation.data[relation.app]
        if not app_data:
            return None
        try:
            return LvmSanBackendData.from_relation_data(app_data)
        except DataValidationError:
            logger.exception("Invalid lvm-san-backend relation payload")
            return None

    @property
    def ready(self) -> bool:
        """Whether relation has valid payload and provider marked backend ready."""
        data = self.backend_data
        return bool(data and data.ready)

    def _on_relation_changed(self, event: RelationChangedEvent) -> None:
        data = self.backend_data
        if not data:
            return
        serialized = data.to_relation_data()
        self.on.changed.emit(backend_data=serialized)
        if data.ready:
            self.on.ready.emit(backend_data=serialized)

    def _on_relation_broken(self, event: RelationBrokenEvent) -> None:
        self.on.goneaway.emit()


class LvmSanBackendProvides(Object):
    """Provider side helper for the ``lvm-san-backend`` relation."""

    on = LvmSanBackendProviderEvents()

    def __init__(
        self,
        charm: CharmBase,
        relation_name: str = DEFAULT_RELATION_NAME,
    ) -> None:
        super().__init__(charm, relation_name)
        self._charm = charm
        self._relation_name = relation_name

        events = self._charm.on[relation_name]
        self.framework.observe(events.relation_joined, self._on_relation_joined)

    def _on_relation_joined(self, _event) -> None:
        self.on.has_clients.emit()

    def set_backend_data(
        self,
        backend_data: LvmSanBackendData,
        relation_id: int | None = None,
    ) -> None:
        """Publish backend data on one relation or all relations."""
        if not self.model.unit.is_leader():
            logger.debug(
                "Skipping %s publish on non-leader unit %s",
                self._relation_name,
                self.model.unit.name,
            )
            return
        payload = backend_data.to_relation_data()
        for relation in self._iter_relations(relation_id):
            try:
                relation.data[self.model.app].update(payload)
            except ModelError as exc:
                logger.warning(
                    "Skipping %s relation %s update due to model error: %s",
                    self._relation_name,
                    relation.id,
                    exc,
                )

    def _iter_relations(self, relation_id: int | None = None) -> list[Relation]:
        relations = self.model.relations.get(self._relation_name, [])
        if relation_id is None:
            return list(relations)
        return [relation for relation in relations if relation.id == relation_id]


def _dump_relation_data(data: Mapping[str, Any]) -> dict[str, str]:
    encoded: dict[str, str] = {}
    for key, value in data.items():
        if isinstance(value, bool):
            encoded[key] = str(value).lower()
        elif isinstance(value, list):
            encoded[key] = json.dumps(value)
        elif value is None:
            continue
        else:
            encoded[key] = str(value)
    return encoded


def _load_relation_data(data: Mapping[str, str]) -> dict[str, Any]:
    loaded: dict[str, Any] = {}
    for key, value in data.items():
        if key in {"vips", "portals"}:
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError as exc:
                raise DataValidationError(
                    f"{key} must be JSON-encoded list"
                ) from exc
            if not isinstance(parsed, list):
                raise DataValidationError(f"{key} must decode to a list")
            loaded[key] = parsed
            continue

        if key == "ready":
            normalized = value.strip().lower()
            if normalized not in {"true", "false"}:
                raise DataValidationError("ready must be one of: true,false")
            loaded[key] = normalized == "true"
            continue

        loaded[key] = value

    return loaded


def _validate_payload(payload: Mapping[str, Any]) -> None:
    _validate_nonempty_string(payload, "backend-key")
    _validate_nonempty_string(payload, "status")

    ready = payload.get("ready")
    if not isinstance(ready, bool):
        raise DataValidationError("ready must be a boolean")

    vips = payload.get("vips")
    if not isinstance(vips, list) or not vips:
        raise DataValidationError("vips must be a non-empty list")
    _validate_endpoint_list(vips, "vips")

    portals = payload.get("portals", [])
    if not isinstance(portals, list):
        raise DataValidationError("portals must be a list")
    _validate_endpoint_list(portals, "portals")

    target_protocol = payload.get("target-protocol", "iscsi")
    if target_protocol not in {"iscsi"}:
        raise DataValidationError("target-protocol must be iscsi")

    auth_type = payload.get("auth-type", "none")
    if auth_type not in {"none", "chap"}:
        raise DataValidationError("auth-type must be one of: none,chap")

    if auth_type == "chap":
        _validate_nonempty_string(payload, "chap-username-secret-id")
        _validate_nonempty_string(payload, "chap-password-secret-id")

    optional_strings = {
        "volume-group",
        "thin-pool",
        "target-helper",
        "preferred-active-unit",
        "active-unit",
        "active-node",
    }
    for key in optional_strings:
        if key in payload and payload[key] is not None:
            _validate_nonempty_string(payload, key)


def _validate_endpoint_list(values: list[Any], key: str) -> None:
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise DataValidationError(f"{key} entries must be non-empty strings")
        try:
            ipaddress.ip_address(value)
        except ValueError as exc:
            raise DataValidationError(
                f"{key} entry {value!r} is not a valid IP address"
            ) from exc


def _validate_nonempty_string(payload: Mapping[str, Any], key: str) -> None:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise DataValidationError(f"{key} must be a non-empty string")
