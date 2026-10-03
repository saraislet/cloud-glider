import importlib.util
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[1] / 'ami/files/prune_kernels.py'
SPEC = importlib.util.spec_from_file_location('prune_kernels', PATH)
prune = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prune)


class KernelPruningTests(unittest.TestCase):
    def test_preserves_selected_kernel_and_modules_and_runtime_meta(self):
        packages = ['linux-image-7.0.0-1014-aws', 'linux-modules-7.0.0-1014-aws',
                    'linux-modules-extra-7.0.0-1014-aws', 'linux-image-aws',
                    'linux-image-7.0.0-1013-aws', 'linux-modules-7.0.0-1013-aws',
                    'linux-headers-aws', 'linux-headers-7.0.0-1014-aws',
                    'linux-aws-7.0-headers-7.0.0-1014', 'cloud-init', 'python3']
        removed = prune.obsolete_packages(packages, '7.0.0-1014-aws')
        self.assertEqual(set(removed), set(packages[4:9]))

    def test_handles_architecture_qualified_packages(self):
        packages = ['linux-image-7.0.0-1014-aws:arm64', 'linux-modules-7.0.0-1014-aws:arm64',
                    'linux-headers-7.0.0-1014-aws:arm64']
        self.assertEqual(prune.obsolete_packages(packages, '7.0.0-1014-aws'), [packages[-1]])

    def test_invalid_or_incomplete_selected_kernel_fails_closed(self):
        for packages, keep in (([], '7.0.0-1014-aws'),
                               (['linux-image-7.0.0-1014-aws'], '7.0.0-1014-aws'),
                               ([], 'generic'), ([], '../kernel')):
            with self.subTest(keep=keep, packages=packages), self.assertRaises(ValueError):
                prune.obsolete_packages(packages, keep)
