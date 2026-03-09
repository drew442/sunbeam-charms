# lvm-san (standalone)

Standalone machine charm for lvm-san that bridges Juju config to snap intent/status
and publishes backend readiness over the `lvm-san-backend` relation.

## Operational actions

This charm exposes Juju actions for real standalone operations:

- `reconcile-now`: run one snap reconcile pass and publish readiness.
- `show-snap-status`: return raw status JSON.
- `san-check`: TCP connectivity check (`nc -zv`) for SAN portals.
- `iscsi-login`: iSCSI discovery/login for configured targets.
- `multipath-report`: return `multipath -ll` output with optional WWID check.
- `pcs-status`: return `pcs status --full`.
- `backend-move`: move backend resource group to target unit.
- `backend-clear-move`: clear backend move constraints.

Run `juju actions lvm-san` for details and required params.

## Config notes

- `iscsi-targets` must be a comma-separated list of IP addresses.
- Do not include `:3260` in `iscsi-targets`; the charm/snap use the default iSCSI port.

## Troubleshooting

- If a unit is stuck after an earlier hook failure, replay hooks and reconcile:
  - `juju resolved lvm-san/<unit>`
  - `juju run lvm-san/<unit> reconcile-now`
