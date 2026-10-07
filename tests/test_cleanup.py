import copy
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


controller = load('cleanup_controller', 'bootstrap/handler.py')
operator = load('lifecycle_operator', 'scripts/lifecycle.py')


class MemoryDdb:
    """Applies the cleanup controller's conditional writes with transactional rollback."""
    def __init__(self):
        self.items = {}
        self.transactions = []

    @staticmethod
    def identity(item):
        return item['PK']['S'], item['SK']['S']

    def check_table(self, kw):
        item = kw.get("Key", kw.get("Item"))
        expected = "cloud-glider-sandbox-generations" if item["PK"]["S"].startswith("GEN#") else "table"
        if item["PK"]["S"] in ("AUDIT#PROPAGATION", "AUDIT#RELEASE"):
            expected = "cloud-glider-sandbox-audit"
        assert kw["TableName"] == expected, (kw["TableName"], expected)

    def get_item(self, **kw):
        self.check_table(kw)
        return {'Item': copy.deepcopy(self.items.get(self.identity(kw['Key']), {}))}

    def transact_get_items(self, **kw):
        return {'Responses': [self.get_item(**entry['Get']) for entry in kw['TransactItems']]}

    def check(self, kw):
        self.check_table(kw)
        item = self.items.get(self.identity(kw.get('Key', kw.get('Item'))), {})
        names, values = kw.get('ExpressionAttributeNames', {}), kw.get('ExpressionAttributeValues', {})
        for term in kw.get('ConditionExpression', '').split(' AND '):
            if not term:
                continue
            if term.startswith('attribute_not_exists('):
                field = term[len('attribute_not_exists('):-1]
                if names.get(field, field) in item:
                    raise RuntimeError('conditional conflict')
            elif ' IN (' in term:
                field, markers = term.split(' IN (')
                if item.get(names.get(field, field)) not in [values[marker] for marker in markers[:-1].split(', ')]:
                    raise RuntimeError('conditional conflict')
            else:
                field, marker = term.split(' = ')
                if item.get(names.get(field, field)) != values[marker]:
                    raise RuntimeError('conditional conflict')

    def update_item(self, **kw):
        self.check(kw)
        item = self.items[self.identity(kw['Key'])]
        for assignment in kw['UpdateExpression'][4:].split(', '):
            name, marker = assignment.split(' = ')
            item[kw['ExpressionAttributeNames'].get(name, name)] = copy.deepcopy(kw['ExpressionAttributeValues'][marker])

    def put_item(self, **kw):
        self.check_table(kw)
        # The bootstrap inventory put uses an absent-or-same-cycle condition.
        identity = self.identity(kw['Item'])
        if identity in self.items:
            if self.items[identity].get('request_id') != kw['ExpressionAttributeValues'][':id']:
                raise RuntimeError('conditional conflict')
        self.items[identity] = copy.deepcopy(kw['Item'])

    def delete_item(self, **kw):
        self.check(kw)
        self.items.pop(self.identity(kw['Key']), None)

    def transact_write_items(self, **kw):
        operations = kw['TransactItems']
        for entry in operations:
            self.check(next(iter(entry.values())))
        for entry in operations:
            action, args = next(iter(entry.items()))
            if action == 'Put':
                self.items[self.identity(args['Item'])] = copy.deepcopy(args['Item'])
            elif action == 'Delete':
                self.items.pop(self.identity(args['Key']), None)
            elif action == 'Update':
                self.update_item(**args)
        self.transactions.append(copy.deepcopy(operations))

    def get_paginator(self, operation):
        assert operation == 'scan'
        def pages(**kw):
            assert kw['TableName'] == 'cloud-glider-sandbox-generations'
            return [{'Items': [copy.deepcopy(item) for key, item in self.items.items() if key[0].startswith('GEN#')]}]
        return Mock(paginate=pages)


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.ddb = MemoryDdb()
        self.env = {'STATE_TABLE': 'table', 'GENERATION_TABLE': 'cloud-glider-sandbox-generations', 'ENVIRONMENT': 'sandbox', 'SERVICE_ROLE_ARN': 'role'}
        self.ddb.items[('CONTROL', 'GLOBAL')] = {**controller.key('CONTROL'), 'environment': {'S': 'sandbox'}, 'generation_table_name': {'S': 'cloud-glider-sandbox-generations'}}
        self.ddb.items[('CURRENT', 'GLOBAL')] = {**controller.key('CURRENT'), 'status': {'S': 'CURRENT'},
            'request_id': {'S': '1'}, 'stack_id': {'S': 'stack-one'}}
        self.ddb.items[('BOOTSTRAP', 'REQUEST')] = {**controller.key('BOOTSTRAP', 'REQUEST'),
            'schema_version': {'S': '2'}, 'request_id': {'S': '1'}, 'status': {'S': 'SUBMITTED'},
            'bootstrap_requested': {'BOOL': True}, 'propagation_enabled': {'BOOL': True},
            'cleanup_requested': {'BOOL': True}, 'cleanup_status': {'S': 'IDLE'}, 'stack_id': {'S': 'stack-one'}}
        self.ddb.items[('GEN#000000', 'STATE')] = {**controller.key('GEN#000000', 'STATE'),
            'request_id': {'S': '1'}, 'stack_id': {'S': 'stack-one'}}
        self.stacks = {'stack-one': {'StackName': 'cloud-glider-sandbox-gen-000000', 'StackId': 'stack-one',
            'StackStatus': 'CREATE_COMPLETE', 'RoleARN': 'role',
            'Parameters': [{'ParameterKey': 'RequestId', 'ParameterValue': '1'}, {'ParameterKey': 'Environment', 'ParameterValue': 'sandbox'}],
            'Tags': [{'Key': key, 'Value': value} for key, value in {
                'project': 'cloud-glider', 'environment': 'sandbox', 'purpose': 'generation-stack', 'bootstrap-request-id': '1'}.items()]}}
        self.cfn = Mock()
        self.cfn.describe_stacks.side_effect = lambda **kw: {'Stacks': [copy.deepcopy(self.stacks[kw['StackName']])]}
        self.cfn.get_paginator.side_effect = lambda operation: Mock(paginate=lambda **kw:
            [{'StackSummaries': [copy.deepcopy(stack) for stack in self.stacks.values()]}] if operation == 'list_stacks'
            else [{'StackResourceSummaries': []}])
        self.cfn.delete_stack.side_effect = lambda **kw: self.stacks[kw['StackName']].update(StackStatus='DELETE_IN_PROGRESS')
        self.ec2 = Mock()
        self.ec2.get_paginator.side_effect = lambda operation: Mock(paginate=lambda **kw:
            [{'Reservations': []}] if operation == 'describe_instances' else ([{'Volumes': []}] if operation == 'describe_volumes' else [{'Snapshots': []}]))
        self.ec2.describe_addresses.return_value = {'Addresses': []}
        self.ec2.describe_snapshots.return_value = {'Snapshots': []}

    @property
    def request(self):
        return self.ddb.items[('BOOTSTRAP', 'REQUEST')]

    def step(self, event=None):
        with patch.object(controller.time, 'time', return_value=100):
            controller.cleanup_step(copy.deepcopy(event or self.request), self.ddb, self.cfn, self.ec2, self.env)

    def test_closes_gates_before_deletion_and_rearms_only_after_verified_completion(self):
        def delete(**kw):
            self.assertEqual(self.request['propagation_enabled'], {'BOOL': False})
            self.assertEqual(self.request['bootstrap_requested'], {'BOOL': False})
            self.assertEqual(self.request['request_id'], {'S': '1'})
            self.assertIn(('GEN#000000', 'STATE'), self.ddb.items)
            self.stacks[kw['StackName']]['StackStatus'] = 'DELETE_IN_PROGRESS'
        self.cfn.delete_stack.side_effect = delete
        self.step()
        self.assertEqual(self.request['cleanup_status'], {'S': 'DELETING'})
        self.assertEqual(self.cfn.delete_stack.call_args.kwargs['StackName'], 'stack-one')
        self.step()  # Duplicate delivery during deletion submits nothing again.
        self.cfn.delete_stack.assert_called_once()
        self.stacks['stack-one']['StackStatus'] = 'DELETE_COMPLETE'
        self.step()
        self.assertEqual(self.request['cleanup_status'], {'S': 'COMPLETE'})
        self.assertEqual(self.request['request_id'], {'S': '2'})
        self.assertEqual(self.request['cleanup_target_request_id'], {'S': '1'})
        self.assertEqual(self.request['status'], {'S': 'READY'})
        self.assertEqual(self.request['cleanup_requested'], {'BOOL': False})
        self.assertNotIn('stack_id', self.request)
        self.assertNotIn(('GEN#000000', 'STATE'), self.ddb.items)
        self.assertEqual(self.ddb.items[('CURRENT', 'GLOBAL')]['status'], {'S': 'UNINITIALIZED'})
        self.assertFalse(any(pk.startswith('AUDIT#CLEANUP') for pk, sk in self.ddb.items))

    def test_stale_event_does_not_touch_new_cycle(self):
        event = copy.deepcopy(self.request)
        self.request['request_id'] = {'S': '2'}
        self.step(event)
        self.cfn.delete_stack.assert_not_called()
        self.assertEqual(self.request['propagation_enabled'], {'BOOL': True})

    def test_provisioning_marker_waits_then_requires_attention_without_reset(self):
        self.ddb.items[('LOCK', 'PROVISIONING')] = {**controller.key('LOCK', 'PROVISIONING'), 'request_id': {'S': '1'}}
        self.step()
        self.assertEqual(self.request['cleanup_status'], {'S': 'QUIESCING'})
        with patch.object(controller.time, 'time', return_value=400):
            with self.assertRaisesRegex(RuntimeError, 'marker did not settle'):
                controller.cleanup_step(copy.deepcopy(self.request), self.ddb, self.cfn, self.ec2, self.env)
        self.assertEqual(self.request['cleanup_status'], {'S': 'NEEDS_ATTENTION'})
        self.assertEqual(self.request['request_id'], {'S': '1'})
        self.cfn.delete_stack.assert_not_called()

    def test_active_create_waits_without_deletion(self):
        self.stacks['stack-one']['StackStatus'] = 'CREATE_IN_PROGRESS'
        self.step()
        self.cfn.delete_stack.assert_not_called()
        self.assertEqual(self.request['cleanup_status'], {'S': 'QUIESCING'})

    def test_delete_failure_retains_state_and_blocks_bootstrap(self):
        self.cfn.delete_stack.side_effect = RuntimeError('denied')
        with self.assertRaisesRegex(RuntimeError, 'denied'):
            self.step()
        self.assertEqual(self.request['cleanup_status'], {'S': 'NEEDS_ATTENTION'})
        self.assertEqual(self.request['bootstrap_requested'], {'BOOL': False})
        self.assertIn(('GEN#000000', 'STATE'), self.ddb.items)
        self.step()
        self.cfn.delete_stack.assert_called_once()

    def test_foreign_stack_prevents_all_deletion(self):
        self.stacks['stack-one']['Tags'][-1]['Value'] = 'old-cycle'
        with self.assertRaisesRegex(RuntimeError, 'ownership mismatch'):
            self.step()
        self.cfn.delete_stack.assert_not_called()

    def test_emergency_hold_survives_complete_cleanup(self):
        hold = {**controller.key('HOLD', 'ACTIVE'), 'active': {'BOOL': True}}
        self.ddb.items[('HOLD', 'ACTIVE')] = copy.deepcopy(hold)
        self.step()
        self.stacks['stack-one']['StackStatus'] = 'DELETE_COMPLETE'
        self.step()
        self.assertEqual(self.ddb.items[('HOLD', 'ACTIVE')], hold)
        self.assertEqual(self.request['propagation_enabled'], {'BOOL': False})

    def test_residual_volume_blocks_reset(self):
        self.step()
        self.stacks['stack-one']['StackStatus'] = 'DELETE_COMPLETE'
        self.ec2.get_paginator.side_effect = lambda operation: Mock(paginate=lambda **kw:
            [{'Reservations': []}] if operation == 'describe_instances' else [{'Volumes': [{'VolumeId': 'leftover'}]}])
        with self.assertRaisesRegex(RuntimeError, 'Residual tagged EBS'):
            self.step()
        self.assertEqual(self.request['request_id'], {'S': '1'})
        self.assertEqual(self.request['cleanup_status'], {'S': 'NEEDS_ATTENTION'})

    def test_delete_failed_stack_prevents_any_new_deletion(self):
        self.stacks['stack-one']['StackStatus'] = 'DELETE_FAILED'
        with self.assertRaisesRegex(RuntimeError, 'DELETE_FAILED'):
            self.step()
        self.cfn.delete_stack.assert_not_called()
        self.assertEqual(self.request['request_id'], {'S': '1'})

    def test_retained_stack_resource_blocks_completion(self):
        self.step()
        self.stacks['stack-one']['StackStatus'] = 'DELETE_COMPLETE'
        self.cfn.get_paginator.side_effect = lambda operation: Mock(paginate=lambda **kw:
            [{'StackSummaries': [copy.deepcopy(stack) for stack in self.stacks.values()]}] if operation == 'list_stacks'
            else [{'StackResourceSummaries': [{'ResourceStatus': 'DELETE_SKIPPED'}]}])
        with self.assertRaisesRegex(RuntimeError, 'retained generation resources'):
            self.step()
        self.assertEqual(self.request['request_id'], {'S': '1'})

    def test_current_conflict_prevents_deletion(self):
        self.ddb.items[('CURRENT', 'GLOBAL')]['request_id'] = {'S': 'another-cycle'}
        with self.assertRaisesRegex(RuntimeError, 'CURRENT belongs'):
            self.step()
        self.cfn.delete_stack.assert_not_called()

    def test_root_volume_id_is_checked_even_without_tags(self):
        self.request['cleanup_volume_ids'] = {'L': [{'S': 'untagged-root'}]}
        self.request['cleanup_status'] = {'S': 'VERIFYING'}
        self.request['propagation_enabled'] = {'BOOL': False}
        self.request['bootstrap_requested'] = {'BOOL': False}
        self.request['cleanup_started_at'] = {'N': '100'}
        self.stacks['stack-one']['StackStatus'] = 'DELETE_COMPLETE'
        self.ec2.describe_volumes.return_value = {'Volumes': [{'VolumeId': 'untagged-root'}]}
        with self.assertRaisesRegex(RuntimeError, 'Residual generation root volume'):
            self.step()
        self.assertEqual(self.request['request_id'], {'S': '1'})

    def test_final_reset_conflict_keeps_launches_blocked(self):
        self.step()
        self.stacks['stack-one']['StackStatus'] = 'DELETE_COMPLETE'
        original = self.ddb.transact_write_items
        def transact(**kw):
            if any('Put' in entry and entry['Put']['Item'].get('status') == {'S': 'UNINITIALIZED'} for entry in kw['TransactItems']):
                raise RuntimeError('reset conflict')
            return original(**kw)
        self.ddb.transact_write_items = transact
        with self.assertRaisesRegex(RuntimeError, 'reset conflict'):
            self.step()
        self.assertEqual(self.request['request_id'], {'S': '1'})
        self.assertEqual(self.request['propagation_enabled'], {'BOOL': False})
        self.assertEqual(self.request['cleanup_status'], {'S': 'NEEDS_ATTENTION'})

    def test_exact_inventory_finds_stack_missing_from_list(self):
        self.cfn.get_paginator.side_effect = lambda operation: Mock(paginate=lambda **kw:
            [{'StackSummaries': []}] if operation == 'list_stacks' else [{'StackResourceSummaries': []}])
        self.step()
        self.cfn.delete_stack.assert_called_once()

    def test_operator_cleanup_closes_both_gates_atomically(self):
        self.request['cleanup_requested'] = {'BOOL': False}
        update = operator.build_update('table', self.request, 'cleanup', 'operator', 100)
        self.ddb.update_item(**update)
        self.assertEqual(self.request['propagation_enabled'], {'BOOL': False})
        self.assertEqual(self.request['bootstrap_requested'], {'BOOL': False})
        self.assertEqual(self.request['cleanup_requested'], {'BOOL': True})

    def test_resume_does_not_erase_inventory(self):
        self.step()
        self.request['cleanup_status'] = {'S': 'NEEDS_ATTENTION'}
        original = copy.deepcopy(self.request['cleanup_stack_ids'])
        update = operator.build_update('table', self.request, 'resume-cleanup', 'operator', 100)
        self.ddb.update_item(**update)
        self.assertEqual(self.request['cleanup_stack_ids'], original)
        self.assertEqual(self.request['request_id'], {'S': '1'})

    def test_trigger_is_only_false_to_true_cleanup(self):
        old = {'cleanup_requested': {'BOOL': False}}
        event = {'eventName': 'MODIFY', 'dynamodb': {'OldImage': old, 'NewImage': copy.deepcopy(self.request)}}
        self.assertTrue(controller.is_cleanup_trigger(event))
        event['dynamodb']['OldImage']['cleanup_requested'] = {'BOOL': True}
        self.assertFalse(controller.is_cleanup_trigger(event))


if __name__ == '__main__':
    unittest.main()
