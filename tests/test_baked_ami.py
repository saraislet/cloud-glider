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
        (self.root / 'daemon').write_bytes(b'approved daemon')
        self.manifest = {'daemon_sha256': 'a' * 64,
                         'files': {'daemon': hashlib.sha256(b'approved daemon').hexdigest()}}
        self.write_manifest()

    def write_manifest(self):
        (self.root / 'etc/cloud-glider/image.json').write_text(json.dumps(self.manifest))

    def test_matching_image_is_accepted(self):
        self.assertEqual(verifier.verify(self.root, 'a' * 64), self.manifest)

    def test_other_approved_artifact_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'approved artifact'):
            verifier.verify(self.root, 'b' * 64)

    def test_modified_daemon_is_rejected(self):
        (self.root / 'daemon').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'baked file differs'):
            verifier.verify(self.root, 'a' * 64)

    def test_missing_file_is_rejected(self):
        (self.root / 'daemon').unlink()
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
                          'project': 'cloud-glider', 'purpose': 'daemon-image', 'daemon-sha256': 'a'*64}.items()],
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


class BakedRuntimeTests(unittest.TestCase):
    def setUp(self):
        with patch.dict(sys.modules, {'verify_image': verifier}):
            self.smoke = load('image_smoke', 'ami/files/smoke_test.py')
        self.prefix = patch.object(self.smoke.sys, 'prefix', '/opt/cloud-glider/venv')
        self.prefix.start()
        self.addCleanup(self.prefix.stop)

    def test_sdk_runtime_accepts_no_aws_executable(self):
        with patch.object(self.smoke.shutil, 'which', side_effect=lambda name: None if name == 'aws' else '/usr/bin/'+name), patch.object(self.smoke.importlib, 'import_module') as imports:
            self.smoke.verify_runtime()
            self.assertEqual([call.args[0] for call in imports.call_args_list], ['boto3', 'botocore'])

    def test_missing_sdk_fails_closed(self):
        with patch.object(self.smoke.shutil, 'which', side_effect=lambda name: None if name == 'aws' else '/usr/bin/'+name), patch.object(self.smoke.importlib, 'import_module', side_effect=ModuleNotFoundError('boto3')):
            with self.assertRaises(ModuleNotFoundError):
                self.smoke.verify_runtime()

    def test_cli_containing_image_rejected(self):
        with patch.object(self.smoke.shutil, 'which', return_value='/usr/bin/tool'):
            with self.assertRaisesRegex(AssertionError, 'AWS CLI unexpectedly'):
                self.smoke.verify_runtime()

    def test_wrong_python_environment_rejected(self):
        with patch.object(self.smoke.sys, 'prefix', '/usr'), patch.object(self.smoke.shutil, 'which', side_effect=lambda name: None if name == 'aws' else '/usr/bin/'+name):
            with self.assertRaisesRegex(AssertionError, 'daemon venv'):
                self.smoke.verify_runtime()

    def test_installed_current_contract_passes(self):
        self.smoke.verify_lifecycle_contract()

    def test_legacy_image_contract_is_rejected(self):
        import dataclasses
        import types
        legacy = types.SimpleNamespace(
            DaemonConfig=dataclasses.make_dataclass('LegacyConfig', [('environment', str)]),
            GENERATION_PARAMETER_NAMES={'Generation'})
        with patch.dict(sys.modules, {'cloud_glider.daemon': legacy}):
            with self.assertRaisesRegex(AssertionError, 'legacy daemon configuration'):
                self.smoke.verify_lifecycle_contract()
