# cinder-volume-lvm – Contributing Guide

## Code overview

The charm is built with the Charmed Operator Framework and the ops_sunbeam helper
library.

CinderVolumeLVMOperatorCharm (in `src/charm.py`) extends
`OSCinderVolumeDriverOperatorCharm` and publishes a single relation –
`cinder-volume` – to inject the LVM backend stanza into `cinder.conf` within the
principal application (usually the cinder-volume snap).

Configuration is rendered through a Jinja2 template shipped inside the
`cinder-volume` snap, so the charm’s responsibility is limited to mapping config
keys and triggering a service restart.

## Build and deploy

From the charm directory:

```
charmcraft pack
```

Deploy the built charm:

```
juju deploy ./cinder-volume-lvm_*.charm --trust
juju relate cinder-volume:cinder-volume cinder-volume-lvm:cinder-volume
```
