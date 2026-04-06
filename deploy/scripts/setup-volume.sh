#!/usr/bin/env bash
# setup-volume.sh — Mount Hetzner Volume for GenLab media storage.
#
# Prerequisites: Create a 50GB volume in Hetzner Console and attach to server.
# The volume device will appear as /dev/disk/by-id/scsi-0HC_Volume_<ID>
#
# Usage: sudo bash deploy/scripts/setup-volume.sh <volume-device>
# Example: sudo bash deploy/scripts/setup-volume.sh /dev/disk/by-id/scsi-0HC_Volume_12345678

set -euo pipefail

DEVICE="${1:?Usage: $0 <volume-device-path>}"
MOUNT="/mnt/genlab-media"

if ! [ -b "$DEVICE" ]; then
    echo "ERROR: $DEVICE is not a block device"
    exit 1
fi

# Format if not already formatted
if ! blkid "$DEVICE" | grep -q ext4; then
    echo "Formatting $DEVICE as ext4..."
    mkfs.ext4 "$DEVICE"
fi

# Mount
mkdir -p "$MOUNT"
mount "$DEVICE" "$MOUNT"

# Add to fstab if not already there
if ! grep -q "$MOUNT" /etc/fstab; then
    echo "$DEVICE $MOUNT ext4 discard,nofail,defaults 0 0" >> /etc/fstab
    echo "Added to /etc/fstab"
fi

# Create directory structure
mkdir -p "$MOUNT"/{clips,rendered,assets,.scores}

# Set ownership
chown -R genlab:genlab "$MOUNT"

echo "Volume mounted at $MOUNT"
df -h "$MOUNT"
