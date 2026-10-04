#!/usr/bin/env python3
"""Read-only metadata gate; passing is not operational release approval."""
import argparse
import json
import subprocess


def validate(image, snapshots, owner, digest):
    expected = {'OwnerId': owner, 'Architecture': 'arm64', 'State': 'available',
                'RootDeviceType': 'ebs', 'RootDeviceName': '/dev/sda1',
                'VirtualizationType': 'hvm', 'BootMode': 'uefi', 'EnaSupport': True,
                'ImdsSupport': 'v2.0', 'Public': False}
    for key, value in expected.items():
        if image.get(key) != value:
            raise ValueError(f'AMI {key} must be {value!r}')
    tags = {t['Key']: t['Value'] for t in image.get('Tags', [])}
    for key, value in {'project': 'cloud-glider', 'purpose': 'daemon-image', 'daemon-sha256': digest}.items():
        if tags.get(key) != value:
            raise ValueError(f'AMI tag {key} differs')
    blocks = image.get('BlockDeviceMappings', [])
    if len(blocks) != 1 or blocks[0].get('DeviceName') != '/dev/sda1':
        raise ValueError('candidate must contain only the approved root disk')
    ebs = blocks[0]['Ebs']
    if ebs.get('VolumeSize') != 2 or ebs.get('VolumeType') != 'gp3' or not ebs.get('DeleteOnTermination'):
        raise ValueError('expected deletable 2 GiB gp3 root')
    if len(snapshots) != 1:
        raise ValueError('expected one root snapshot')
    snap = snapshots[0]
    for key, value in {'SnapshotId': ebs['SnapshotId'], 'OwnerId': owner,
                       'State': 'completed', 'VolumeSize': 2, 'Encrypted': True}.items():
        if snap.get(key) != value:
            raise ValueError(f'snapshot {key} must be {value!r}')
    return {'image_id': image['ImageId'], 'snapshot_id': snap['SnapshotId'],
            'metadata_checks_passed': True, 'release_approved': False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--image-id', required=True)
    p.add_argument('--expected-owner', required=True)
    p.add_argument('--daemon-sha256', required=True)
    a = p.parse_args()
    def aws(*args):
        return json.loads(subprocess.check_output(['aws', *args, '--region', 'us-west-2', '--output', 'json']))
    images = aws('ec2', 'describe-images', '--image-ids', a.image_id)['Images']
    if len(images) != 1:
        raise ValueError('expected exactly one AMI')
    ids = [b['Ebs']['SnapshotId'] for b in images[0]['BlockDeviceMappings'] if 'Ebs' in b]
    if len(ids) != 1:
        raise ValueError('expected exactly one root snapshot')
    snapshots = aws('ec2', 'describe-snapshots', '--snapshot-ids', ids[0])['Snapshots']
    result = validate(images[0], snapshots, a.expected_owner, a.daemon_sha256)
    permissions = aws('ec2', 'describe-image-attribute', '--image-id', a.image_id,
                      '--attribute', 'launchPermission').get('LaunchPermissions', [])
    if permissions:
        raise ValueError('candidate has external launch permissions')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
