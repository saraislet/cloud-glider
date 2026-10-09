import unittest
from unittest.mock import Mock

from test_cleanup import controller


class AlarmBatchTests(unittest.TestCase):
    def inventory(self, count):
        return {f'i-{n:017x}': {'PK': {'S': f'GEN#r{n}'}}
                for n in range(count)}

    def client(self, resources):
        expected = {'cloud-glider-sandbox-gen-' + record['PK']['S'][4:] + '-status-check': iid
                    for iid, record in resources.items()}
        client = Mock()
        client.describe_alarms.side_effect = lambda **kw: {'MetricAlarms': [
            {'AlarmName': name, 'Namespace': 'AWS/EC2', 'MetricName': 'StatusCheckFailed',
             'Dimensions': [{'Name': 'InstanceId', 'Value': expected[name]}]}
            for name in kw['AlarmNames']]}
        return client

    def test_63_instance_cleanup_uses_one_verified_delete_batch(self):
        resources = self.inventory(63); client = self.client(resources); guard = Mock()
        controller.cleanup_status_alarms(client, resources, resources, 'sandbox', guard)
        client.describe_alarms.assert_called_once()
        client.delete_alarms.assert_called_once()
        self.assertEqual(len(client.delete_alarms.call_args.kwargs['AlarmNames']), 63)
        self.assertEqual(guard.call_count, 2)

    def test_all_batches_validate_before_any_deletion(self):
        resources = self.inventory(101); client = self.client(resources)
        original = client.describe_alarms.side_effect
        def describe(**kw):
            result = original(**kw)
            if len(kw['AlarmNames']) == 1:
                result['MetricAlarms'][0]['Dimensions'][0]['Value'] = 'foreign'
            return result
        client.describe_alarms.side_effect = describe
        with self.assertRaisesRegex(RuntimeError, 'ownership'):
            controller.cleanup_status_alarms(client, resources, resources, 'sandbox', Mock())
        client.delete_alarms.assert_not_called()

    def test_each_batch_is_fenced_and_limited_to_100(self):
        resources = self.inventory(201); client = self.client(resources); guard = Mock()
        controller.cleanup_status_alarms(client, resources, resources, 'sandbox', guard)
        self.assertEqual([len(c.kwargs['AlarmNames']) for c in client.delete_alarms.call_args_list], [100, 100, 1])
        self.assertEqual(guard.call_count, 6)

    def test_stop_or_changed_cycle_prevents_deletion(self):
        resources = self.inventory(2); client = self.client(resources)
        guard = Mock(side_effect=[None, RuntimeError('cycle changed')])
        with self.assertRaisesRegex(RuntimeError, 'cycle changed'):
            controller.cleanup_status_alarms(client, resources, resources, 'sandbox', guard)
        client.delete_alarms.assert_not_called()

    def test_missing_alarms_are_idempotent_but_ambiguous_results_fail(self):
        resources = self.inventory(2); client = Mock()
        client.describe_alarms.return_value = {'MetricAlarms': []}
        controller.cleanup_status_alarms(client, resources, resources, 'sandbox', Mock())
        client.delete_alarms.assert_not_called()
        for result in ({'CompositeAlarms': [{}]}, {'NextToken': 'unexpected'}):
            client.describe_alarms.return_value = result
            with self.assertRaisesRegex(RuntimeError, 'ambiguous'):
                controller.cleanup_status_alarms(client, resources, resources, 'sandbox', Mock())
        client.delete_alarms.assert_not_called()
