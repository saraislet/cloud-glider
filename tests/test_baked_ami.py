import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


verifier = load('image_verifier', 'ami/files/verify_image.py')
metadata = load('image_metadata', 'scripts/validate_baked_ami.py')


class ImageIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'etc/cloud-glider').mkdir(parents=True)
        (self.root / 'agent').write_bytes(b'approved agent')
        self.manifest = {'agent_sha256': 'a' * 64,
                         'files': {'agent': hashlib.sha256(b'approved agent').hexdigest()}}
        self.write_manifest()

    def write_manifest(self):
        (self.root / 'etc/cloud-glider/image.json').write_text(json.dumps(self.manifest))

    def test_matching_image_is_accepted(self):
        self.assertEqual(verifier.verify(self.root, 'a' * 64), self.manifest)

    def test_other_approved_artifact_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'approved artifact'):
            verifier.verify(self.root, 'b' * 64)

    def test_modified_agent_is_rejected(self):
        (self.root / 'agent').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'baked file differs'):
            verifier.verify(self.root, 'a' * 64)

    def test_missing_file_is_rejected(self):
        (self.root / 'agent').unlink()
        with self.assertRaises(FileNotFoundError):
            verifier.verify(self.root, 'a' * 64)

    def test_empty_or_traversing_manifest_is_rejected(self):
        for files in ({}, {'../outside': 'a'}, {'/etc/passwd': 'a'}):
            self.manifest['files'] = files
            self.write_manifest()
            with self.assertRaises(ValueError):
                verifier.verify(self.root, 'a' * 64)


class ImageMetadataTests(unittest.TestCase):
    def setUp(self):
        self.image = {'ImageId': 'ami-candidate', 'OwnerId': '123456789012',
                      'Architecture': 'arm64', 'State': 'available', 'RootDeviceType': 'ebs',
                      'RootDeviceName': '/dev/sda1', 'VirtualizationType': 'hvm',
                      'BootMode': 'uefi', 'EnaSupport': True, 'ImdsSupport': 'v2.0', 'Public': False,
                      'Tags': [{'Key': k, 'Value': v} for k, v in {
                          'project': 'cloud-glider', 'purpose': 'agent-image', 'agent-sha256': 'a'*64}.items()],
                      'BlockDeviceMappings': [{'DeviceName': '/dev/sda1', 'Ebs': {
                          'SnapshotId': 'snap-root', 'VolumeSize': 2, 'VolumeType': 'gp3',
                          'DeleteOnTermination': True}}]}
        self.snapshot = {'SnapshotId': 'snap-root', 'OwnerId': '123456789012',
                         'State': 'completed', 'VolumeSize': 2, 'Encrypted': True}

    def check(self):
        return metadata.validate(self.image, [self.snapshot], '123456789012', 'a'*64)

    def test_valid_metadata_does_not_approve_release(self):
        result = self.check()
        self.assertTrue(result['metadata_checks_passed'])
        self.assertFalse(result['release_approved'])

    def test_public_wrong_owner_architecture_or_digest_rejected(self):
        original = copy.deepcopy(self.image)
        for field, value in [('Public', True), ('OwnerId', 'other'), ('Architecture', 'x86_64'),
                             ('BootMode', 'legacy-bios'), ('ImdsSupport', 'v1.0'), ('Tags', [])]:
            self.image = {**original, field: value}
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.check()

    def test_oversized_or_unencrypted_snapshot_rejected(self):
        for field, value in [('VolumeSize', 8), ('Encrypted', False), ('SnapshotId', 'other')]:
            original = dict(self.snapshot)
            self.snapshot[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.check()
            self.snapshot = original

    def test_extra_volume_rejected(self):
        self.image['BlockDeviceMappings'].append({'DeviceName': '/dev/sdf'})
        with self.assertRaises(ValueError):
            self.check()


