#!/bin/bash
# Only the one blank 2 GiB non-root EBS disk is eligible for formatting.
set -euo pipefail
root=/mnt/cloud-glider-root
mapfile -t disks < <(lsblk -dnpo NAME,TYPE,SIZE -b | awk '$2 == "disk" && $3 == 2147483648 {print $1}')
test "${#disks[@]}" -eq 1 || { echo 'Expected exactly one 2 GiB target disk' >&2; exit 1; }
disk=${disks[0]}
test "$(lsblk -nrpo NAME "$disk" | wc -l)" -eq 1
! lsblk -nrpo MOUNTPOINT "$disk" | grep -q '[^[:space:]]'
test -z "$(wipefs --noheadings --output TYPE "$disk")"
sgdisk --clear --new=1:2048:+128M --typecode=1:ef00 --new=2:0:0 --typecode=2:8300 "$disk"
partprobe "$disk"
udevadm settle
mapfile -t partitions < <(lsblk -nrpo NAME,TYPE "$disk" | awk '$2 == "part" {print $1}')
test "${#partitions[@]}" -eq 2
efi=${partitions[0]}
fs=${partitions[1]}
mkfs.vfat -F 32 "$efi"
mkfs.ext4 -m 1 "$fs"
mkdir -p "$root"
mount "$fs" "$root"
cleanup() { umount -R "$root"; }
trap cleanup EXIT
rsync -aHAXx --numeric-ids / "$root/" \
  --exclude=/dev/* --exclude=/proc/* --exclude=/sys/* --exclude=/run/* \
  --exclude=/tmp/* --exclude=/mnt/* --exclude=/media/* --exclude=/lost+found \
  --exclude=/boot/efi/* --exclude=/home/* --exclude=/root/* \
  --exclude=/var/lib/cloud/* --exclude=/var/lib/amazon/* --exclude=/var/lib/snapd/* \
  --exclude=/var/log/* --exclude=/var/cache/apt/* --exclude=/var/lib/apt/lists/*
# /boot may be a separate filesystem on the source AMI, so the root rsync
# above deliberately does not traverse it. Copy it explicitly, excluding the
# source ESP and GRUB configuration; both are rebuilt for the target disk.
rsync -aHAXx --numeric-ids /boot/ "$root/boot/" --exclude=/efi/*** --exclude=/grub/***
shopt -s nullglob
kernels=("$root"/boot/vmlinuz-*)
shopt -u nullglob
test "${#kernels[@]}" -gt 0 || { echo 'Target /boot contains no kernel' >&2; exit 1; }
for kernel in "${kernels[@]}"; do
  version=${kernel##*/vmlinuz-}
  test -s "$kernel" && test -d "$root/lib/modules/$version" || {
    echo "Missing target kernel or modules for $version" >&2; exit 1;
  }
done
mkdir -p "$root/boot/efi"
mount "$efi" "$root/boot/efi"
printf 'UUID=%s / ext4 defaults 0 1\nUUID=%s /boot/efi vfat umask=0077 0 1\n' \
  "$(blkid -s UUID -o value "$fs")" "$(blkid -s UUID -o value "$efi")" > "$root/etc/fstab"
for path in dev proc sys run; do
  mount --rbind "/$path" "$root/$path"
  mount --make-rslave "$root/$path"
done
# Override the source image's 40-force-partuuid.cfg: the target has a new UUID.
# Use normal initramfs boot and avoid importing host boot entries.
printf 'GRUB_FORCE_PARTUUID=\nGRUB_DISABLE_LINUX_UUID=false\nGRUB_DISABLE_OS_PROBER=true\nGRUB_CMDLINE_LINUX="console=tty0 console=ttyAMA0,115200n8"\n' > "$root/etc/default/grub.d/99-cloud-glider.cfg"
chroot "$root" update-initramfs -u -k all
chroot "$root" grub-install --target=arm64-efi --efi-directory=/boot/efi --bootloader-id=ubuntu --removable --no-nvram
chroot "$root" update-grub
# The removable ARM64 loader may start with its prefix on the ESP. Give
# that lookup path a UUID-based bridge to the root filesystem's real config.
mkdir -p "$root/boot/efi/boot/grub"
printf 'search --no-floppy --fs-uuid --set=root %s\nset prefix=($root)/boot/grub\nconfigfile $prefix/grub.cfg\n' \
  "$(blkid -s UUID -o value "$fs")" > "$root/boot/efi/boot/grub/grub.cfg"
chroot "$root" grub-script-check /boot/efi/boot/grub/grub.cfg
# Successful tool exit codes alone do not prove a bootable image was assembled.
test -s "$root/boot/efi/EFI/BOOT/BOOTAA64.EFI"
chroot "$root" grub-script-check /boot/grub/grub.cfg
for kernel in "${kernels[@]}"; do
  version=${kernel##*/vmlinuz-}
  test -s "$root/boot/initrd.img-$version" || {
    echo "Missing target initramfs for $version" >&2; exit 1;
  }
  chroot "$root" lsinitramfs "/boot/initrd.img-$version" >/dev/null
  grep -F "vmlinuz-$version" "$root/boot/grub/grub.cfg" | grep '^[[:space:]]*linux' >/dev/null || {
    echo "GRUB has no kernel entry for $version" >&2; exit 1;
  }
  grep -F "initrd.img-$version" "$root/boot/grub/grub.cfg" | grep '^[[:space:]]*initrd' >/dev/null || {
    echo "GRUB has no initramfs entry for $version" >&2; exit 1;
  }
done
grep -Fq "root=UUID=$(blkid -s UUID -o value "$fs")" "$root/boot/grub/grub.cfg" || {
  echo 'GRUB does not reference the target root UUID' >&2; exit 1;
}
# Builder access is not enabled on generation instances. A smoke-test stack can
# explicitly enable SSM with its own test-only role.
systemctl --root="$root" disable amazon-ssm-agent.service ssh.service ssh.socket
chroot "$root" dpkg-query -W > "$root/etc/cloud-glider/packages.txt"
# Cloud-init recreates network configuration and instance-specific keys.
rm -f "$root/etc/netplan/50-cloud-init.yaml"
install -d -m 0750 -o "$(id -u ubuntu)" -g "$(id -g ubuntu)" "$root/home/ubuntu"
# New instances must receive fresh identities; no baked launch config or keys.
rm -f "$root/etc/cloud-glider/bootstrap.json" "$root/etc/ssh/ssh_host_"* "$root/var/lib/systemd/random-seed"
truncate -s 0 "$root/etc/machine-id"
rm -f "$root/var/lib/dbus/machine-id"
ln -s /etc/machine-id "$root/var/lib/dbus/machine-id"
# Do not preserve any Packer SSH key in cloud-init's default account.
rm -f "$root/etc/sudoers.d/90-cloud-init-users"
test ! -e "$root/etc/systemd/system/multi-user.target.wants/cloud-glider.service"
# Leave at least 384 MiB for logs, temporary data, and maintenance.
available=$(df --output=avail -B1 "$root" | tail -1)

df -h "$root"
du -xhd1 "$root/usr" "$root/var" "$root/opt" | sort -h
du -xhd1 "$root/usr/local" "$root/usr/lib" | sort -h
chroot "$root" dpkg-query -W \
  -f='${Installed-Size}\t${binary:Package}\n' |
  sort -nr | sed -n '1,30p'
printf 'Available bytes: %s; required: 402653184\n' "$available"

test "$available" -ge 402653184 || {
  echo 'Insufficient 2 GiB root headroom' >&2
  exit 1
}
sync
