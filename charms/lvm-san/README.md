# lvm-san (interface stub)

This directory currently hosts the `lvm-san-backend` relation library for
Milestone M1.

## `lvm-san-backend` v0 schema

Provider app data keys:

- `backend-key` (string, required): stable backend identifier.
- `vips` (json list of IP strings, required): stable VIP endpoints.
- `portals` (json list of IP strings, optional): explicit iSCSI portals.
- `volume-group` (string, optional): LVM volume group for the backend.
- `thin-pool` (string, optional): thin pool name when thin provisioning is used.
- `target-helper` (string, optional): target helper (for example `lioadm`).
- `target-protocol` (string, default `iscsi`).
- `auth-type` (`none` or `chap`, default `none`).
- `chap-username-secret-id` / `chap-password-secret-id` (string, required when `auth-type=chap`).
- `preferred-active-unit` / `active-unit` (optional strings).
- `ready` (boolean encoded as `true`/`false`, required): backend readiness gate.
- `status` (string, required): human-readable status message.

Notes:

- Secret values are never sent directly in relation data; only secret references
  are allowed.
- The requirer should only consume backend configuration when `ready=true`.
