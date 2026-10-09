import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from observer.server import Store, Observer, project, iso
from botocore.exceptions import ClientError
import threading
import datetime as dt

class ObserverTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.tmp.name)/'events.sqlite')
    def tearDown(self):
        self.store.db.close();self.tmp.cleanup()
    def rows(self):
        return [dict(PK='CURRENT',SK='GLOBAL',generation='000001',instance_id='i-b',request_id='1',status='CURRENT',
                     predecessor_instance_id='i-a',retirement_authorized=True,retirement_completed=False),
                dict(PK='GEN#000000',SK='STATE',generation='000000',instance_id='i-a',request_id='1',status='CANDIDATE',heartbeat_at_epoch=100),
                dict(PK='BOOTSTRAP',SK='REQUEST',propagation_enabled=False)]
    def test_paused_collector_makes_no_aws_calls(self):
        o=Observer.__new__(Observer)
        o.stop=threading.Event();o.active=threading.Event();o.collection_lock=threading.RLock()
        o.status={'state':'paused'};o.shards={};o.tables=['table'];o.ddb=Mock();o.poll=Mock();o.scan=Mock();o.reconcile_instances=Mock()
        thread=threading.Thread(target=o.run);thread.start()
        try:
            self.assertFalse(o.set_active(False)['collection_active'])
            o.ddb.describe_table.assert_not_called();o.scan.assert_not_called();o.poll.assert_not_called();o.reconcile_instances.assert_not_called()
            self.assertTrue(o.set_active(True)['collection_active'])
            self.assertFalse(o.set_active(False)['collection_active'])
        finally:o.stop.set();thread.join(2)

    def test_physical_termination_overrides_retained_readiness(self):
        rows=self.rows()+[dict(PK='EC2',SK='i-a',instance_id='i-a',ec2_state='terminated')]
        node=next(n for n in project(rows,'us-west-2') if n['aws_instance_id']=='i-a')
        self.assertEqual(node['state'],'TERMINATED')
        self.assertEqual(node['ec2_state'],'terminated')

    def test_termination_survives_current_advancing(self):
        rows=self.rows()
        rows[0]['retirement_completed']=True
        self.store.update('test',[(r,r) for r in rows],'us-west-2','one')
        rows[0]['predecessor_instance_id']='i-other'
        self.store.update('test',[(r,r) for r in rows],'us-west-2','two')
        node=next(n for n in self.store.snapshot()['instances'] if n['aws_instance_id']=='i-a')
        self.assertEqual(node['state'],'TERMINATED')

    def test_stop_does_not_hide_instances(self):
        nodes=project(self.rows(),'us-west-2')
        self.assertEqual(len(nodes),2)
        parent=next(n for n in nodes if n['aws_instance_id']=='i-a')
        self.assertEqual(parent['state'],'DRAINING')
        self.assertEqual(parent['cleanup_owner_id'],'1:i-b')
    def test_hold_overrides_readiness(self):
        rows=self.rows()+[dict(PK='HOLD',SK='ACTIVE')]
        self.assertTrue(all(n['state']=='EMERGENCY_HOLD' for n in project(rows,'r')))
    def test_running_or_heartbeat_is_not_readiness(self):
        row=dict(PK='GEN#000000',SK='STATE',generation='0',instance_id='i-a',status='CANDIDATE',workload_healthy=True,ec2_state='running')
        self.assertEqual(project([row],'r')[0]['state'],'BOOTING')
    def test_confirmed_retirement(self):
        rows=self.rows();rows[0]['retirement_completed']=True
        parent=next(n for n in project(rows,'r') if n['aws_instance_id']=='i-a')
        self.assertEqual(parent['state'],'TERMINATED')
    def test_atomic_checkpoint_dedup_and_restart(self):
        rows=self.rows()
        self.store.update('t',[(r,r) for r in rows],'r','a',raw={'event':'a'},checkpoint=('arn','shard','123',0))
        first=self.store.events()
        self.store.update('t',[(r,r) for r in rows],'r','a',raw={'event':'a'},checkpoint=('arn','shard','123',0))
        self.assertEqual(len(first),len(self.store.events()))
        self.assertEqual(self.store.checkpoint('arn','shard'),('123',0))
        reopened=Store(Path(self.tmp.name)/'events.sqlite')
        self.assertEqual(reopened.snapshot(),self.store.snapshot());reopened.db.close()
    def test_delete_does_not_infer_termination(self):
        rows=self.rows();self.store.update('t',[(r,r) for r in rows],'r','a')
        self.store.update('t',[],'r','b',replace=True)
        nodes=self.store.snapshot()['instances']
        self.assertTrue(all(n['record_removed'] for n in nodes))
        self.assertTrue(all(n['state']!='TERMINATED' for n in nodes))
    def test_failed_handoff_and_cycle_fencing(self):
        rows=self.rows();rows[0]['request_id']='other'
        nodes=project(rows,'r')
        self.assertEqual(next(n for n in nodes if n['instance_id']=='1:i-a')['state'],'BOOTING')
        self.assertIsNone(next(n for n in nodes if n['instance_id']=='1:i-a').get('cleanup_owner_id'))
    def test_stream_order_and_checkpoint(self):
        obs=Observer.__new__(Observer);obs.store=self.store;obs.region='r';obs.streams=Mock();obs.shards={('arn','shard'):('t','iterator')};obs.watermarks={};obs.status={'tables':{},'warnings':[]}
        row=self.rows()[1]
        from boto3.dynamodb.types import TypeSerializer
        encode=lambda r:{k:TypeSerializer().serialize(v) for k,v in r.items()}
        obs.streams.get_records.return_value={'Records':[{'eventID':'e','dynamodb':{'Keys':encode({'PK':row['PK'],'SK':row['SK']}),'NewImage':encode(row),'SequenceNumber':'4'}}]}
        obs.poll()
        self.assertEqual(self.store.checkpoint('arn','shard'),('4',1))
        self.assertEqual(len(self.store.snapshot()['instances']),1)
    def test_transaction_rollback(self):
        with self.assertRaises(KeyError):
            self.store.update('t',[({}, {})],'r','bad',raw={'a':1},checkpoint=('arn','s','5',0))
        self.assertIsNone(self.store.checkpoint('arn','s'))
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM raw_events').fetchone()[0],0)

    def test_snapshot_reconnect_preserves_lifetime_and_history(self):
        row=self.rows()[1]
        self.store.update('t',[(row,row)],'r','first')
        first=self.store.snapshot()['instances'][0]
        ready={**row,'functional_readiness':{'producer_instance_id':row['instance_id']}}
        self.store.update('t',[(ready,ready)],'r','ready')
        ready_node=self.store.snapshot()['instances'][0]
        heartbeat={**ready,'heartbeat_at_epoch':200}
        self.store.update('t',[(heartbeat,heartbeat)],'r','heartbeat')
        last=self.store.snapshot()['instances'][0]
        self.assertEqual(last['created_at'],first['created_at'])
        self.assertEqual(last['ready_at'],ready_node['ready_at'])
        self.assertEqual([h['state'] for h in last['history']],['BOOTING','READY'])
        count=len(self.store.events())
        self.store.update('t',[(heartbeat,heartbeat)],'r','unchanged')
        self.assertEqual(len(self.store.events()),count)

    def test_empty_open_shard_restart_uses_trim_horizon(self):
        obs=self.make_observer();obs.shards={('arn','s'):('t','iterator')}
        obs.streams.get_records.return_value={'Records':[],'NextShardIterator':'next'}
        obs.poll()
        self.assertEqual(self.store.checkpoint('arn','s'),(None,0))
        obs.shards.clear()
        obs.streams.describe_stream.return_value={'StreamDescription':{'Shards':[{'ShardId':'s'}]}}
        obs.streams.get_shard_iterator.return_value={'ShardIterator':'restart'}
        obs.discover('t','arn')
        self.assertEqual(obs.streams.get_shard_iterator.call_args.kwargs['ShardIteratorType'],'TRIM_HORIZON')
        self.assertNotIn('SequenceNumber',obs.streams.get_shard_iterator.call_args.kwargs)
    def test_stream_captures_insert_modify_remove_between_snapshots(self):
        obs=self.make_observer();obs.shards={('arn','s'):('generations','iterator')};obs.watermarks={'generations':100}
        row=self.rows()[1];ready={**row,'functional_readiness':{'producer_instance_id':row['instance_id']}}
        from boto3.dynamodb.types import TypeSerializer
        encode=lambda r:{k:TypeSerializer().serialize(v) for k,v in r.items()}
        records=[]
        for i,value in enumerate((row,ready,None),1):
            data={'Keys':encode({'PK':row['PK'],'SK':row['SK']}),'ApproximateCreationDateTime':dt.datetime.fromtimestamp(100+i,dt.timezone.utc),'SequenceNumber':str(i)}
            if value is not None:data['NewImage']=encode(value)
            records.append({'eventID':str(i),'dynamodb':data})
        obs.streams.get_records.return_value={'Records':records,'NextShardIterator':'next'}
        obs.poll()
        self.assertEqual([e['state'] for _,e in self.store.events()],['BOOTING','READY','WAITING'])
        self.assertTrue(self.store.snapshot()['instances'][0]['record_removed'])
        self.assertEqual(self.store.checkpoint('arn','s'),('3',0))
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM raw_events').fetchone()[0],3)
        obs.poll()  # Duplicate delivery must not revert records or append replay events.
        self.assertEqual(len(self.store.events()),3)
        self.assertEqual(self.store.checkpoint('arn','s'),('3',0))

    def make_observer(self):
        obs=Observer.__new__(Observer)
        obs.store=self.store;obs.region='r';obs.streams=Mock();obs.ddb=Mock()
        obs.shards={};obs.watermarks={};obs.stop=threading.Event()
        obs.status={'state':'starting','tables':{},'warnings':[]}
        return obs
    def test_epoch_zero(self):
        self.assertEqual(iso(0),'1970-01-01T00:00:00+00:00')
    def test_delayed_duplicate_preserves_latest_state_and_checkpoint(self):
        row=self.rows()[1]
        self.store.update('t',[(row,row)],'r','first',raw={'e':1},checkpoint=('arn','s','10',0))
        newer={**row,'heartbeat_at_epoch':200}
        self.store.update('t',[(newer,newer)],'r','second',raw={'e':2},checkpoint=('arn','s','11',0))
        self.store.update('t',[(row,row)],'r','first',raw={'e':1},checkpoint=('arn','s','10',0))
        self.assertEqual(self.store.checkpoint('arn','s'),('11',0))
        self.assertEqual(self.store.snapshot()['instances'][0]['heartbeat_at'],iso(200))
        self.assertEqual(len(self.store.events()),2)
    def test_current_error_not_ready(self):
        rows=self.rows();rows[0]['status']='ERROR'
        node=next(n for n in project(rows,'r') if n['aws_instance_id']=='i-b')
        self.assertEqual(node['state'],'ERROR')
        self.assertEqual(next(n for n in project(rows,'r') if n['aws_instance_id']=='i-a')['state'],'BOOTING')
    def test_paginated_scan_archives_complete_snapshot(self):
        obs=self.make_observer()
        from boto3.dynamodb.types import TypeSerializer
        encode=lambda r:{k:TypeSerializer().serialize(v) for k,v in r.items()}
        rows=self.rows()
        obs.ddb.get_paginator.return_value.paginate.return_value=[{'Items':[encode(rows[0])]},{'Items':[encode(r) for r in rows[1:]]}]
        obs.scan('t')
        obs.ddb.get_paginator.return_value.paginate.assert_called_once_with(TableName='t',ConsistentRead=True)
        self.assertEqual(len(self.store.snapshot()['instances']),2)
        raw=json.loads(self.store.db.execute('SELECT data FROM raw_events').fetchone()[0])
        self.assertEqual(raw['snapshot'],rows)
    def test_shard_parent_before_child_and_restart_checkpoint(self):
        obs=self.make_observer()
        obs.streams.describe_stream.return_value={'StreamDescription':{'Shards':[{'ShardId':'parent'},{'ShardId':'child','ParentShardId':'parent'}]}}
        obs.streams.get_shard_iterator.return_value={'ShardIterator':'p'}
        obs.discover('t','arn')
        self.assertEqual(list(obs.shards),[('arn','parent')])
        obs.streams.get_records.return_value={'Records':[]}
        obs.poll()
        obs.discover('t','arn')
        self.assertEqual(list(obs.shards),[('arn','child')])
        self.store.update('t',[],'r','checkpoint',checkpoint=('arn','child','100',0))
        obs.shards.clear();obs.discover('t','arn')
        self.assertEqual(obs.streams.get_shard_iterator.call_args.kwargs['ShardIteratorType'],'AFTER_SEQUENCE_NUMBER')
        self.assertEqual(obs.streams.get_shard_iterator.call_args.kwargs['SequenceNumber'],'100')
    def test_trimmed_checkpoint_reports_gap_and_resumes(self):
        obs=self.make_observer()
        self.store.update('t',[],'r','checkpoint',checkpoint=('arn','s','100',0))
        obs.streams.describe_stream.return_value={'StreamDescription':{'Shards':[{'ShardId':'s'}]}}
        obs.streams.get_shard_iterator.side_effect=[ClientError({'Error':{'Code':'TrimmedDataAccessException'}},'GetShardIterator'),{'ShardIterator':'oldest'}]
        obs.discover('t','arn')
        self.assertTrue(obs.status['warnings'])
        self.assertEqual(obs.streams.get_shard_iterator.call_args.kwargs['ShardIteratorType'],'TRIM_HORIZON')
        self.assertNotIn('SequenceNumber',obs.streams.get_shard_iterator.call_args.kwargs)
    def test_iterator_expiration_preserves_sequence(self):
        obs=self.make_observer();obs.shards={('arn','s'):('t','expired')}
        self.store.update('t',[],'r','checkpoint',checkpoint=('arn','s','100',0))
        obs.streams.get_records.side_effect=ClientError({'Error':{'Code':'ExpiredIteratorException'}},'GetRecords')
        obs.poll()
        self.assertEqual(obs.shards,{})
        self.assertEqual(self.store.checkpoint('arn','s'),('100',0))
    def test_stream_denied_keeps_polling_available(self):
        obs=self.make_observer();obs.shards={('arn','s'):('t','iterator')}
        obs.streams.get_records.side_effect=ClientError({'Error':{'Code':'AccessDeniedException'}},'GetRecords')
        obs.poll()
        self.assertIn('snapshot polling',obs.status['tables']['t'])
        self.assertTrue(obs.status['warnings'])
    def test_old_stream_record_cannot_regress_snapshot(self):
        obs=self.make_observer();obs.shards={('arn','s'):('t','iterator')};obs.watermarks={'t':200}
        row=self.rows()[1];self.store.update('t',[(row,row)],'r','snapshot')
        from boto3.dynamodb.types import TypeSerializer
        encode=lambda r:{k:TypeSerializer().serialize(v) for k,v in r.items()}
        obs.streams.get_records.return_value={'Records':[{'eventID':'old','dynamodb':{'Keys':encode({'PK':row['PK'],'SK':row['SK']}),'ApproximateCreationDateTime':dt.datetime.fromtimestamp(100,dt.timezone.utc),'SequenceNumber':'5'}}]}
        obs.poll()
        self.assertFalse(self.store.snapshot()['instances'][0].get('record_removed'))
        self.assertEqual(self.store.checkpoint('arn','s'),('5',1))
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM raw_events').fetchone()[0],1)

if __name__=='__main__': unittest.main()

class FamilyProjectionTests(unittest.TestCase):
    def rows(self):
        return [dict(PK='GEN#r0', SK='NODE',request_id='1',instance_id='i-child',configuration_sha256='digest',owner='i-child',status='OWNER',handoff_at=102),
                dict(PK='GEN#r0', SK='STATE',request_id='1',instance_id='i-child',configuration_sha256='digest',node_path='r0',generation='000001',predecessor_instance_id='i-root',daemon_live=True,ready_at=105,first_ready_at=101,continuation='DRY_RUN_PASSED'),
                dict(PK='GEN#r0',SK='RESOURCE#i-child',request_id='1',instance_id='i-child'),
                dict(PK='EC2',SK='i-child',instance_id='i-child',ec2_state='running',launch_at=iso(90))]
    def test_family_join_order_readiness_and_source_timing(self):
        import itertools
        for rows in itertools.permutations(self.rows()):
            event=project(rows,'us-west-2')[0]
            self.assertEqual(event['state'],'READY')
            self.assertEqual(event['ready_at'],iso(101))
            self.assertEqual(event['created_at'],iso(90))
            self.assertEqual(event['ownership_at'],iso(102))
            self.assertEqual(event['heartbeat_at'],iso(105))
            self.assertEqual(event['parent_id'],'1:i-root')
    def test_daemon_readiness_and_live_propagation_are_distinct(self):
        rows = self.rows(); rows[1]['continuation'] = 'DAEMON_READY'
        event = project(rows, 'r')[0]
        self.assertEqual(event['state'], 'READY')
        self.assertFalse(event['propagation_demonstrated'])
        self.assertIn('not yet demonstrated', event['readiness_basis'])
        receipt = dict(PK='GEN#r0', SK='PROPAGATION', request_id='1', instance_id='i-child',
                       node_path='r0', configuration_sha256='digest', result='LIVE_LAUNCH_PASSED',
                       children=[dict(node_path='r00', instance_id='i-grandchild', client_token='token')], demonstrated_at=106)
        event = project(rows + [receipt], 'r')[0]
        self.assertTrue(event['propagation_demonstrated'])
        self.assertEqual(event['propagation_at'], iso(106))
        self.assertEqual(event['ready_at'], iso(101))
        receipt['configuration_sha256'] = 'foreign'
        self.assertFalse(project(rows + [receipt], 'r')[0]['propagation_demonstrated'])

    def test_family_conflicting_proof_never_claims_ready(self):
        rows=self.rows(); rows[1]['configuration_sha256']='other'
        self.assertEqual(project(rows,'r')[0]['state'],'BOOTING')
    def test_family_stop_retirement_and_termination_precedence(self):
        rows=self.rows()+[dict(PK='GEN#r0',SK='STOP',request_id='1')]
        self.assertEqual(project(rows,'r')[0]['state'],'WAITING')
        rows[0]['status']='RETIRING'
        self.assertEqual(project(rows,'r')[0]['state'],'DRAINING')
        rows[3]['ec2_state']='terminated'
        self.assertEqual(project(rows,'r')[0]['state'],'TERMINATED')
    def test_persisted_family_history_and_first_readiness_do_not_shift(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory)/'events.sqlite')
            rows=self.rows();store.update('t',[(r,r)for r in rows],'r','first')
            first=store.snapshot()['instances'][0]
            rows[1]['ready_at']=110
            store.update('t',[(rows[1],rows[1])],'r','refresh')
            second=store.snapshot()['instances'][0]
            self.assertEqual(first['ready_at'],second['ready_at'])
            self.assertEqual(first['created_at'],second['created_at'])
            self.assertEqual(len(second['history']),1)
            rows[0]['status']='RETIRING';store.update('t',[(rows[0],rows[0])],'r','retire')
            self.assertEqual([h['state']for h in store.snapshot()['instances'][0]['history']],['READY','DRAINING'])
            store.db.close()
