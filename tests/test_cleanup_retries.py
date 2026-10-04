import copy
import importlib.util
import sys
import unittest
from types import ModuleType
from unittest.mock import Mock, patch

import test_operator_controls as operator_tests
from test_cleanup import controller


class CleanupRetryTests(unittest.TestCase):
    def setUp(self):
        fixture = operator_tests.OperatorControlTests()
        fixture.setUp()
        self.ddb, self.cfn, self.s3, self.ec2 = fixture.ddb, fixture.cfn, fixture.s3, fixture.ec2
        self.env = {**fixture.env, 'CLEANUP_SCHEDULE_GROUP': 'cloud-glider-sandbox-cleanup',
            'FUNCTION_ARN': 'arn:aws:lambda:us-west-2:111122223333:function:cloud-glider-sandbox-bootstrap',
            'CLEANUP_RETRY_ROLE_ARN': 'arn:aws:iam::111122223333:role/cloud-glider-sandbox-cleanup-retry'}
        self.scheduler = Mock()
        self.request = self.ddb.items[('BOOTSTRAP', 'REQUEST')]
        self.request.update(cleanup_requested={'BOOL': True}, cleanup_status={'S': 'DELETING'})
        self.control = self.ddb.items[('CONTROL', 'GLOBAL')]
        self.control.update(active_command={'S': 'CLEANUP'}, active_request_id={'S': '1'})

    def arm(self):
        with patch.object(controller.time, 'time', return_value=100):
            controller.schedule_cleanup_retry(self.ddb, self.scheduler, self.env, '1')

    def retry(self, token='cleanup-1-1', request_id='1'):
        with patch.object(controller.time, 'time', return_value=160):
            controller.retry_cleanup({'request_id': request_id, 'retry_token': token},
                self.ddb, self.cfn, self.s3, self.ec2, self.scheduler, self.env)

    def test_one_time_schedule_auto_deletes_and_is_scoped_to_cleanup_cycle(self):
        self.arm()
        args = self.scheduler.create_schedule.call_args.kwargs
        self.assertEqual(args['ScheduleExpression'], 'at(1970-01-01T00:02:40)')
        self.assertEqual(args['ActionAfterCompletion'], 'DELETE')
        self.assertEqual(args['FlexibleTimeWindow'], {'Mode': 'OFF'})
        self.assertEqual(args['Name'], 'cleanup-1-1')
        self.assertEqual(args['Target']['Arn'], self.env['FUNCTION_ARN'])
        self.assertEqual(args['Target']['RoleArn'], self.env['CLEANUP_RETRY_ROLE_ARN'])
        self.assertIn('"request_id": "1"', args['Target']['Input'])
        self.assertEqual(args['Target']['RetryPolicy']['MaximumRetryAttempts'], 2)

    def test_duplicate_initial_delivery_reuses_same_idempotent_schedule(self):
        self.arm()
        first = self.scheduler.create_schedule.call_args.kwargs
        self.arm()
        self.assertEqual(self.scheduler.create_schedule.call_args.kwargs, first)
        self.assertEqual(self.request['cleanup_retry_sequence'], {'N': '1'})

    def test_idle_bootstrap_and_attention_never_schedule(self):
        for status in ('IDLE', 'COMPLETE', 'NEEDS_ATTENTION'):
            self.request['cleanup_status'] = {'S': status}
            self.arm()
        self.scheduler.create_schedule.assert_not_called()

    def test_successful_retry_advances_once_and_duplicate_cannot_rearm(self):
        self.arm()
        with patch.object(controller, 'run_operator') as work:
            self.retry()
            self.assertEqual(self.request['cleanup_retry_token'], {'S': 'cleanup-1-2'})
            self.retry()
            work.assert_called_once()
        self.assertEqual(self.scheduler.create_schedule.call_count, 2)

    def test_stale_cycle_or_missing_identity_does_no_work(self):
        self.arm()
        with patch.object(controller, 'run_operator') as work:
            for request_id in ('2', None, '', 1):
                self.retry(request_id=request_id)
            work.assert_not_called()
        self.assertEqual(self.scheduler.create_schedule.call_count, 1)

    def test_attention_delivery_and_resume_invalidate_old_token(self):
        self.arm()
        self.request['cleanup_status'] = {'S': 'NEEDS_ATTENTION'}
        with patch.object(controller, 'run_operator') as work:
            self.retry()
            work.assert_not_called()
        self.request.update(cleanup_status={'S': 'QUIESCING'}, cleanup_retry_token={'S': ''})
        self.arm()
        self.assertEqual(self.request['cleanup_retry_token'], {'S': 'cleanup-1-2'})
        with patch.object(controller, 'run_operator') as work:
            self.retry()
            work.assert_not_called()

    def test_completion_cancels_pending_and_never_schedules_new_cycle(self):
        self.arm()
        def complete(*args):
            self.request.update(request_id={'S': '2'}, cleanup_requested={'BOOL': False}, cleanup_status={'S': 'COMPLETE'})
        with patch.object(controller, 'run_operator', side_effect=complete):
            self.retry()
        self.assertEqual(self.scheduler.create_schedule.call_count, 1)
        self.scheduler.delete_schedule.assert_called_once_with(GroupName=self.env['CLEANUP_SCHEDULE_GROUP'], Name='cleanup-1-1')

    def test_timeout_keeps_token_for_bounded_delivery_retry(self):
        self.arm()
        with patch.object(controller, 'run_operator', side_effect=KeyboardInterrupt('timeout')):
            with self.assertRaises(KeyboardInterrupt):
                self.retry()
        self.assertEqual(self.request['cleanup_retry_token'], {'S': 'cleanup-1-1'})
        self.assertEqual(self.request['cleanup_retry_sequence'], {'N': '1'})

    def test_schedule_failure_stops_and_reports_attention(self):
        self.scheduler.create_schedule.side_effect = RuntimeError('scheduler unavailable')
        with self.assertRaisesRegex(RuntimeError, 'scheduler unavailable'):
            controller.finish_cleanup_delivery(self.ddb, self.scheduler, self.env, copy.deepcopy(self.request))
        self.assertEqual(self.request['cleanup_status'], {'S': 'NEEDS_ATTENTION'})
        self.assertEqual(self.control['operation_status'], {'S': 'NEEDS_ATTENTION'})
        self.assertIn('scheduling failed', self.control['last_result']['S'])

    def test_completion_during_schedule_creation_cancels_raced_schedule(self):
        def complete(**kw):
            self.request.update(request_id={'S': '2'}, cleanup_status={'S': 'COMPLETE'}, cleanup_requested={'BOOL': False})
        self.scheduler.create_schedule.side_effect = complete
        self.arm()
        self.scheduler.delete_schedule.assert_called_once()

    def test_conflicting_schedule_requires_inspection(self):
        class Conflict(Exception):
            response = {'Error': {'Code': 'ConflictException'}}
        self.scheduler.create_schedule.side_effect = Conflict()
        self.scheduler.get_schedule.return_value = {'Target': {'Arn': 'foreign-function'}}
        with self.assertRaisesRegex(RuntimeError, 'conflicts'):
            controller.finish_cleanup_delivery(self.ddb, self.scheduler, self.env, copy.deepcopy(self.request))
        self.assertEqual(self.request['cleanup_status'], {'S': 'NEEDS_ATTENTION'})

    def test_matching_schedule_conflict_is_idempotent(self):
        self.arm()
        args = self.scheduler.create_schedule.call_args.kwargs
        class Conflict(Exception):
            response = {'Error': {'Code': 'ConflictException'}}
        self.scheduler.create_schedule.side_effect = Conflict()
        self.scheduler.get_schedule.return_value = args
        self.arm()
        self.assertEqual(self.request['cleanup_retry_sequence'], {'N': '1'})

    def test_controller_has_no_recurring_event_route(self):
        clients = dict(zip(('dynamodb', 'cloudformation', 's3', 'ec2', 'scheduler'),
            (self.ddb, self.cfn, self.s3, self.ec2, self.scheduler)))
        sdk = ModuleType('boto3')
        sdk.client = Mock(side_effect=lambda name, **kw: clients[name])
        config = ModuleType('botocore.config')
        config.Config = Mock()
        with patch.dict(sys.modules, {'boto3': sdk, 'botocore': ModuleType('botocore'), 'botocore.config': config}), patch.dict('os.environ', {**self.env, 'AWS_REGION': 'us-west-2'}), patch.object(controller, 'run_operator') as work:
            controller.handler({'source': 'aws.events', 'detail-type': 'Scheduled Event'}, None)
        work.assert_not_called()
        self.scheduler.create_schedule.assert_not_called()

    def test_fired_pending_schedule_is_not_recreated(self):
        self.arm()
        with patch.object(controller.time, 'time', return_value=200):
            controller.schedule_cleanup_retry(self.ddb, self.scheduler, self.env, '1')
        self.assertEqual(self.scheduler.create_schedule.call_count, 1)

    def test_resume_helper_is_a_stream_trigger_and_invalidates_token(self):
        from test_cleanup import operator
        self.request.update(cleanup_status={'S': 'NEEDS_ATTENTION'}, cleanup_retry_token={'S': 'cleanup-1-1'})
        old = copy.deepcopy(self.request)
        self.ddb.update_item(**operator.build_update('table', self.request, 'resume-cleanup', 'operator', 100))
        self.assertEqual(self.request['cleanup_retry_token'], {'S': ''})
        record = {'eventName': 'MODIFY', 'dynamodb': {'OldImage': old, 'NewImage': self.request}}
        self.assertTrue(controller.is_cleanup_trigger(record))

    @unittest.skipIf(importlib.util.find_spec('boto3') is None,
        'Install daemon/requirements.txt to run SDK transport tests')
    def test_scheduler_request_passes_sdk_model_validation(self):
        import boto3
        from botocore.stub import Stubber
        self.arm()
        args = self.scheduler.create_schedule.call_args.kwargs
        client = boto3.client('scheduler', region_name='us-west-2', aws_access_key_id='test', aws_secret_access_key='test')
        with Stubber(client) as stub:
            stub.add_response('create_schedule', {'ScheduleArn': 'arn:aws:scheduler:us-west-2:111122223333:schedule/cloud-glider-sandbox-cleanup/cleanup-1-1'}, args)
            client.create_schedule(**args)
            stub.assert_no_pending_responses()
