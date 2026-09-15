import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "validate_generation_inputs.py"
SPEC = importlib.util.spec_from_file_location("validate_generation_inputs", MODULE_PATH)
validator = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(validator)


class GenerationInputTests(unittest.TestCase):
    def image(self, **overrides):
        image = {
            "ImageId": "ami-0123456789abcdef0",
            "Architecture": "arm64",
            "State": "available",
            "RootDeviceType": "ebs",
            "VirtualizationType": "hvm",
        }
        image.update(overrides)
        return image

    def test_accepts_arm64_t4g_in_us_west_2(self):
        result = validator.validate_image("us-west-2", "t4g.micro", [self.image()])
        self.assertEqual(result["Architecture"], "arm64")

    def test_rejects_wrong_region(self):
        with self.assertRaises(ValueError):
            validator.validate_image("us-east-1", "t4g.micro", [self.image()])

    def test_rejects_x86_64(self):
        with self.assertRaises(ValueError):
            validator.validate_image("us-west-2", "t4g.micro", [self.image(Architecture="x86_64")])

    def test_rejects_other_instance_type(self):
        with self.assertRaises(ValueError):
            validator.validate_image("us-west-2", "t3.micro", [self.image()])


if __name__ == "__main__":
    unittest.main()
