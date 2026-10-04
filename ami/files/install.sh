#!/bin/bash
# Runs on the disposable ARM64 builder, before copying to the target disk.
set -euo pipefail
: "${DAEMON_SHA256:?}" "${SOURCE_COMMIT:?}"
test "$(uname -m)" = aarch64
. /etc/os-release
test "$ID" = ubuntu && test "$VERSION_ID" = 24.04
if cloud-init status --wait --long; then
  :
else
  rc=$?
  tail -n 150 /var/log/cloud-init-output.log >&2
  exit "$rc"
fi
# Use the Ubuntu ports HTTPS endpoint; regional EC2 mirrors may not serve HTTPS.
# Preserve suites, components, and Signed-By settings in the deb822 source file.
sed -E -i 's|https?://([a-z0-9-]+\.)?ec2\.ports\.ubuntu\.com/ubuntu-ports/?|https://ports.ubuntu.com/ubuntu-ports|g; s|http://ports\.ubuntu\.com/ubuntu-ports|https://ports.ubuntu.com/ubuntu-ports|g' /etc/apt/sources.list.d/ubuntu.sources
systemctl stop apt-daily.timer apt-daily-upgrade.timer
export DEBIAN_FRONTEND=noninteractive
export PIP_NO_CACHE_DIR=1
apt-get -o APT::Update::Error-Mode=any update
apt-get -o DPkg::Lock::Timeout=120 install -y --no-install-recommends python3 python3-venv ca-certificates \
  curl rsync gdisk parted dosfstools e2fsprogs grub-efi-arm64-bin linux-aws
cd /tmp/glider-image
printf '%s  daemon.tar.gz\n' "$DAEMON_SHA256" | sha256sum -c -
# The baked runtime uses boto3; reject a source image that already contains AWS CLI.
if command -v aws >/dev/null 2>&1 || [ -e /usr/local/aws-cli ]; then
  echo 'Baked source AMI must not contain AWS CLI' >&2
  exit 1
fi
install -d -m 0750 /opt/cloud-glider /etc/cloud-glider /var/lib/cloud-glider /usr/local/lib/cloud-glider
# The repository artifact contains regular files only. Reject traversal and links.
python3 - <<'PY'
import tarfile
from pathlib import PurePosixPath
with tarfile.open('daemon.tar.gz') as archive:
    for member in archive.getmembers():
        p = PurePosixPath(member.name)
        if not member.isfile() or p.is_absolute() or '..' in p.parts:
            raise SystemExit('unsafe daemon archive')
    archive.extractall('/opt/cloud-glider', filter='data')
PY
install -m 0644 daemon.tar.gz /opt/cloud-glider/daemon.tar.gz
python3 -m venv /opt/cloud-glider/venv
/opt/cloud-glider/venv/bin/pip install --disable-pip-version-check -r /opt/cloud-glider/requirements.txt
/opt/cloud-glider/venv/bin/pip check
/opt/cloud-glider/venv/bin/python -c "import boto3, botocore"
install -m 0644 cloud-glider.service /etc/systemd/system/cloud-glider.service
install -m 0755 verify_image.py /usr/local/lib/cloud-glider/verify_image.py
install -m 0755 smoke_test.py /usr/local/lib/cloud-glider/smoke_test.py
systemctl daemon-reload
systemctl disable cloud-glider.service
python3 - <<'PY'
import hashlib,json,os,subprocess,tarfile
from pathlib import Path
paths=['opt/cloud-glider/daemon.tar.gz','etc/systemd/system/cloud-glider.service',
       'usr/local/lib/cloud-glider/verify_image.py','usr/local/lib/cloud-glider/smoke_test.py']
with tarfile.open('/opt/cloud-glider/daemon.tar.gz') as archive:
    paths += ['opt/cloud-glider/'+member.name for member in archive.getmembers()]
manifest={'schema_version':1,'daemon_sha256':os.environ['DAEMON_SHA256'].lower(),
 'source_commit':os.environ['SOURCE_COMMIT'],
 'files':{p:hashlib.sha256(Path('/'+p).read_bytes()).hexdigest() for p in paths}}
Path('/etc/cloud-glider/image.json').write_text(json.dumps(manifest,indent=2)+'\n')
Path('/etc/cloud-glider/packages.txt').write_bytes(subprocess.check_output(['dpkg-query','-W']))
Path('/etc/cloud-glider/python-packages.txt').write_bytes(subprocess.check_output(['/opt/cloud-glider/venv/bin/pip','freeze','--all']))
PY
/usr/local/lib/cloud-glider/verify_image.py --expected-sha256 "$DAEMON_SHA256"
/opt/cloud-glider/venv/bin/python /opt/cloud-glider/bin/cloud-glider --help
for command in python3 bash uname install cat chmod sha256sum tar gzip systemctl; do command -v "$command"; done
systemd-analyze verify /etc/systemd/system/cloud-glider.service
# Keep the EC2 kernel; generic kernels and build-only packages consume scarce space.
mapfile -t generic < <(dpkg-query -W -f='${binary:Package}\n' | grep -E '^linux-(image|modules)(-extra)?-[0-9].*-generic$|^linux-image-virtual$' || true)
if [ "${#generic[@]}" -gt 0 ]; then apt-get purge -y "${generic[@]}"; fi
# Preserve the selected target AWS kernel and its modules; headers are build-only.
selected_kernel=$(readlink -f /boot/vmlinuz)
selected_kernel=${selected_kernel##*/vmlinuz-}
test -s "/boot/vmlinuz-$selected_kernel" && test -d "/lib/modules/$selected_kernel"
dpkg-query -W -f='${binary:Package} ${db:Status-Abbrev}\n' |
  awk '$2 == "ii" {print $1}' > /tmp/glider-image/installed-kernels.txt
python3 /tmp/glider-image/prune_kernels.py "$selected_kernel" < /tmp/glider-image/installed-kernels.txt > /tmp/glider-image/obsolete-kernels.txt
mapfile -t obsolete < /tmp/glider-image/obsolete-kernels.txt
if [ "${#obsolete[@]}" -gt 0 ]; then apt-get purge -y "${obsolete[@]}"; fi
test -s "/boot/vmlinuz-$selected_kernel" && test -d "/lib/modules/$selected_kernel"
apt-get purge -y snapd
apt-get purge -y liblzo2-2 squashfs-tools
apt-get clean
