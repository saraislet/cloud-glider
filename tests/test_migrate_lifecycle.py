import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
spec = importlib.util.spec_from_file_location('migration', ROOT / 'scripts/migrate_lifecycle.py')
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


class MigrationTests(unittest.TestCase):
    def transaction(self, enabled=False, schema=None):
        control = {'PK': {'S': 'CONTROL'}, 'SK': {'S': 'GLOBAL'}, 'environment': {'S': 'sandbox'},
                   'propagation_enabled': {'BOOL': enabled}}
        current = {'PK': {'S': 'CURRENT'}, 'SK': {'S': 'GLOBAL'}, 'status': {'S': 'CURRENT'},
                   'generation': {'S': '000001'}, 'stack_id': {'S': 'old-stack'}, 'instance_id': {'S': 'old-instance'}}
        request = {'PK': {'S': 'BOOTSTRAP'}, 'SK': {'S': 'REQUEST'}, 'request_id': {'S': 'legacy-uuid'}}
        if schema:
            request['schema_version'] = {'S': schema}
        return migration.build_transaction('table', control, current, request, [], 'operator', 100)

    def test_migration_removes_old_switch_and_resets_identity_atomically(self):
        transaction = self.transaction()
        self.assertNotIn('propagation_enabled', transaction[2]['Put']['Item'])
        self.assertNotIn('stack_id', transaction[3]['Put']['Item'])
        lifecycle = transaction[4]['Put']['Item']
        self.assertEqual(lifecycle['request_id'], {'S': '1'})
        self.assertEqual(lifecycle['propagation_enabled'], {'BOOL': False})
        self.assertEqual(lifecycle['cleanup_requested'], {'BOOL': False})
        self.assertEqual(transaction[-1]['Put']['Item']['actor'], {'S': 'operator'})
        self.assertFalse(any(next(iter(item.values())).get('Key', {}).get('PK') == {'S': 'HOLD'} for item in transaction))

    def test_migration_requires_disabled_legacy_propagation(self):
        with self.assertRaisesRegex(ValueError, 'Disable legacy propagation'):
            self.transaction(enabled=True)

    def test_migration_cannot_reset_an_existing_cycle_counter(self):
        with self.assertRaisesRegex(ValueError, 'already migrated'):
            self.transaction(schema='2')


if __name__ == '__main__':
    unittest.main()
