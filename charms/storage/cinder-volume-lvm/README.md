# cinder-volume-lvm

`cinder-volume-lvm` is a **subordinate** Juju charm that integrates the
`cinder-volume` snap with the Linux LVM backend. It renders a backend stanza
(`lvm.<app-name>.*`) in *cinder.conf* and manages driver options via Juju
config.

## Deployment

```
juju deploy cinder-volume-lvm --trust
juju relate cinder-volume:cinder-volume cinder-volume-lvm:cinder-volume
```

## Configuration

Example configuration for a local LVM VG with iSCSI transport:

```
juju config cinder-volume-lvm \
  volume-group=cinder-volumes \
  lvm-type=auto \
  target-helper=tgtadm \
  target-protocol=iscsi
```

## Notes

* The target volume group must exist on the cinder-volume host(s).
* If you extend volumes that have linked snapshots, LVM may deactivate the
  LV; ensure your `lvm.conf` allows auto-activation of the Cinder VG.

## Relations

`cinder-volume-lvm` requires one relation:

* `cinder-volume` (required)
* `tracing` (optional)
