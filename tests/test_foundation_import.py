import copy
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "verify_foundation_import", ROOT / "scripts/verify_foundation_import.py"
)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class ImportChangeSetTests(unittest.TestCase):
    def test_template_hash_accepts_json_object_or_string(self):
        self.assertEqual(module.template_hash({"b": 2, "a": 1}),
                         module.template_hash('{"a":1,"b":2}'))
        self.assertNotEqual(module.template_hash({"a": 1}),
                            module.template_hash({"a": 2}))

    def setUp(self):
        self.manifest = [{"LogicalResourceId": "Role", "ResourceType": "AWS::IAM::Role"}]
        self.change_set = {
            "StackId": "expected-stack", "Status": "CREATE_COMPLETE",
            "ExecutionStatus": "AVAILABLE",
            "Changes": [{"ResourceChange": dict(self.manifest[0], Action="Import")}],
        }

    def check(self, value):
        module.validate_change_set(value, self.manifest, "expected-stack")

    def test_exact_import_can_be_checked_repeatedly(self):
        self.check(self.change_set)
        self.check(self.change_set)

    def test_rejects_non_import_actions(self):
        for action in ("Add", "Modify", "Remove"):
            with self.subTest(action=action):
                value = copy.deepcopy(self.change_set)
                value["Changes"][0]["ResourceChange"]["Action"] = action
                with self.assertRaises(ValueError):
                    self.check(value)

    def test_rejects_missing_extra_and_duplicate_resources(self):
        for changes in ([], self.change_set["Changes"] * 2,
                        [{"ResourceChange": {"Action": "Import", "LogicalResourceId": "Other"}}]):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.check(dict(self.change_set, Changes=changes))

    def test_rejects_wrong_stack_failed_or_executed_change_set(self):
        for patch in ({"StackId": "other"}, {"Status": "FAILED"},
                      {"ExecutionStatus": "EXECUTE_COMPLETE"},
                      {"ExecutionStatus": "EXECUTE_IN_PROGRESS"},
                      {"ExecutionStatus": "OBSOLETE"}):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                self.check(dict(self.change_set, **patch))
