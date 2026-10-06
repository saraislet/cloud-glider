import os
from pathlib import Path
import sys
import textwrap
import types
import unittest
from unittest.mock import Mock, patch


class FamilyHoldTests(unittest.TestCase):
    def setUp(self):
        text = (Path(__file__).resolve().parents[1] / 'cfn/foundation.yaml').read_text()
        section = text.split('  EmergencyHoldFunction:\n', 1)[1].split('  EmergencyHoldErrorsAlarm:', 1)[0]
        source = textwrap.dedent(section.split('        ZipFile: |\n', 1)[1])
        self.ddb = Mock()
        self.ddb.get_item.return_value = {}
        sdk = types.ModuleType('boto3')
        sdk.client = lambda name: self.ddb
        self.namespace = {}
        with patch.dict(sys.modules, {'boto3': sdk}):
            exec(compile(source, 'emergency_hold_inline', 'exec'), self.namespace)
        self.event = {'request_id': '1', 'generation': '000001', 'node_path': 'r0',
                      'error_code': 'READINESS_ERROR', 'correlation_id': 'test'}

    def invoke(self, event):
        with patch.dict(os.environ, {'TABLE_NAME': 'controls', 'ENVIRONMENT': 'sandbox',
                                     'GENERATION_TABLE': 'generations'}):
            return self.namespace['handler'](event, types.SimpleNamespace(invoked_function_arn='test-function'))

    def test_family_error_uses_lineage_and_legacy_error_uses_generation(self):
        self.invoke(self.event)
        operations = self.ddb.transact_write_items.call_args.kwargs['TransactItems']
        update = next(op['Update'] for op in operations if 'Update' in op)
        self.assertEqual(update['Key']['PK'], {'S': 'GEN#r0'})
        legacy = {k: v for k, v in self.event.items() if k != 'node_path'}
        self.invoke(legacy)
        operations = self.ddb.transact_write_items.call_args.kwargs['TransactItems']
        update = next(op['Update'] for op in operations if 'Update' in op)
        self.assertEqual(update['Key']['PK'], {'S': 'GEN#000001'})

    def test_invalid_lineage_never_creates_hold_or_updates_another_node(self):
        for path in ('r', 'r00', 'foreign', 'r' + '0' * 10):
            with self.assertRaises(ValueError): self.invoke({**self.event, 'node_path': path})
        self.ddb.transact_write_items.assert_not_called()
