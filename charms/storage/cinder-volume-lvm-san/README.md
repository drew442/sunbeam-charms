# cinder-volume-lvm-san

`cinder-volume-lvm-san` is a subordinate charm that contributes an LVM backend
stanza to `cinder-volume` based on data published by the `lvm-san` machine
charm over the `lvm-san-backend` relation.

## Relations

- Requires `cinder-volume` (interface: `cinder-volume`)
- Requires `lvm-san-backend` (interface: `lvm-san-backend`)

The charm only marks the backend ready on `cinder-volume` when
`lvm-san-backend` reports `ready=true`.
