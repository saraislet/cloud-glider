import hashlib
import json
import sys
import threading
import time
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'daemon'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from cloud_glider.timing_export import Exporter, sanitize

class Sink:
    def __init__(self): self.records=[]
    def create_log_stream(self, **kwargs): pass
    def put_log_events(self, **kwargs):
        self.records.extend(json.loads(x['message']) for x in kwargs['logEvents'])
        return {}

class TimingExportTests(unittest.TestCase):
    def test_records_and_completion_digest_prove_delivery(self):
        sink=Sink(); export=Exporter(sink,'group','stream',{'request_id':'1'},interval=.001)
        for n in range(10): export.enqueue({'event':'api_timing','operation':'GetItem','duration_seconds':n,'parameters':{'secret':'forbidden'},'exception':'secret'})
        self.assertTrue(export.close(1)); marker=sink.records[-1]; records=sink.records[:-1]
        self.assertTrue(marker['complete']); self.assertEqual(marker['accepted'],10)
        self.assertEqual(len({r['record_id'] for r in records}),10)
        digest=hashlib.sha256()
        for r in records: digest.update(json.dumps(r,sort_keys=True,separators=(',',':')).encode()+b'\n'); self.assertNotIn('parameters',r); self.assertNotIn('exception',r)
        self.assertEqual(marker['sha256'],digest.hexdigest())
    def test_hung_sink_bounds_enqueue_memory_and_shutdown(self):
        release=threading.Event()
        class Hung(Sink):
            def create_log_stream(self,**kwargs): release.wait(5)
        export=Exporter(Hung(),'group','stream',{},capacity=2,max_bytes=4096)
        start=time.monotonic()
        for n in range(100): export.enqueue({'event':'boot_timing','phase':'startup'})
        self.assertLess(time.monotonic()-start,.1); self.assertEqual(export.accepted,2); self.assertEqual(export.dropped,98)
        start=time.monotonic(); self.assertFalse(export.close(.01)); self.assertLess(time.monotonic()-start,.1); release.set(); export.done.wait(1)
    def test_failed_or_rejected_upload_never_claims_complete(self):
        for response in [None, {'rejectedLogEventsInfo':{'tooOldLogEventEndIndex':0}}]:
            class Failed(Sink):
                def put_log_events(self,**kwargs):
                    if response is None: raise OSError('do not expose credentials')
                    return response
            sink=Failed(); export=Exporter(sink,'group','stream',{},interval=.001); export.enqueue({'event':'phase_timing'}); self.assertTrue(export.close(1)); self.assertGreater(export.errors,0); self.assertEqual(export.uploaded,0)
    def test_unknown_record_and_oversize_fields_are_not_exported(self):
        self.assertIsNone(sanitize({'event':'terminal_error','reason':'secret'}))
        self.assertNotIn('phase',sanitize({'event':'boot_timing','phase':'a'*1000}))
        self.assertNotIn('duration_seconds',sanitize({'event':'boot_timing','duration_seconds':float('nan')}))
    def test_closed_collector_cannot_accept_more_records(self):
        sink=Sink(); export=Exporter(sink,'group','stream',{},interval=.001); self.assertTrue(export.close(1)); self.assertFalse(export.enqueue({'event':'api_timing'}))

class TimingReceiptTests(unittest.TestCase):
    def records(self):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
        from verify_timing_collection import validate
        sink=Sink(); identity={k:'known' for k in ['environment','request_id','generation','instance_id','boot_id','source_commit','daemon_sha256']}
        export=Exporter(sink,'group','stream',identity,interval=.001)
        export.enqueue({'event':'api_timing','operation':'GetItem'}); export.close(1)
        return validate,sink.records
    def test_identical_duplicates_are_deduplicated(self):
        validate,records=self.records();self.assertEqual(validate(records+records,require_boot=False)['records'],1)
    def test_missing_marker_sequence_correlation_and_corruption_fail(self):
        import copy
        validate,records=self.records()
        cases=[records[:-1],records[1:]]
        bad=copy.deepcopy(records);bad[0]['operation']='Corrupted';cases.append(bad)
        bad=copy.deepcopy(records);bad[0].pop('instance_id');cases.append(bad)
        for case in cases:
            with self.assertRaises(ValueError):validate(case,require_boot=False)
        with self.assertRaisesRegex(ValueError,'MissingBootPhase'):validate(records)
    def test_expected_instance_set_rejects_missing_or_unexpected_stream(self):
        validate,records=self.records(); expected=[{k:'known' for k in ['instance_id','generation','request_id','source_commit','daemon_sha256']}]
        self.assertTrue(validate(records,require_boot=False,expected=expected)['complete'])
        expected.append({**expected[0],'instance_id':'missing'})
        with self.assertRaisesRegex(ValueError,'MissingOrUnexpected'):validate(records,require_boot=False,expected=expected)

class TimingPaginationTests(unittest.TestCase):
    def test_empty_pages_and_duplicates_are_read_until_token_stabilizes(self):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
        from verify_timing_collection import retrieve
        class Pages:
            def describe_log_streams(self,**kw):
                if 'nextToken' not in kw:return {'logStreams':[], 'nextToken':'streams2'}
                return {'logStreams':[{'logStreamName':'timing/r/000000/i/b'}]}
            def get_log_events(self,**kw):
                token=kw.get('nextToken')
                return {'events':[] if token=='two' else [{'message':'{"event":"test"}'}], 'nextForwardToken':'two' if token else 'one'}
        self.assertEqual(len(retrieve(Pages(),'group',[dict(request_id='r',generation='000000',instance_id='i')])),2)
    def test_absent_entire_stream_fails(self):
        from verify_timing_collection import retrieve
        class Empty:
            def describe_log_streams(self,**kw):return {'logStreams':[]}
        with self.assertRaisesRegex(ValueError,'MissingTimingStream'):
            retrieve(Empty(),'group',[dict(request_id='r',generation='000000',instance_id='i')])

class CollectorInitializationTests(unittest.TestCase):
    def test_client_initialization_can_complete_after_shutdown_requested(self):
        release=threading.Event(); sink=Sink()
        def factory():release.wait(1);return sink
        export=Exporter(factory,'group','stream',{},interval=.001)
        self.assertFalse(export.close(.001));release.set();self.assertTrue(export.done.wait(1))
        self.assertEqual(export.dropped,0)
        self.assertTrue(sink.records[-1]['complete'])
        self.assertEqual(sink.records[0]['phase'],'collector_client_initialization')

class FailedStartupTests(unittest.TestCase):
    def test_failed_verifier_keeps_original_exception_and_spools_failed_record(self):
        import importlib.util
        import tempfile
        from unittest.mock import patch
        spec=importlib.util.spec_from_file_location('failed_verifier',Path(__file__).resolve().parents[1]/'ami/files/verify_image.py')
        verifier=importlib.util.module_from_spec(spec);spec.loader.exec_module(verifier)
        real_path=Path
        with tempfile.TemporaryDirectory() as directory:
            root=real_path(directory);(root/'proc/sys/kernel/random').mkdir(parents=True)
            (root/'proc/sys/kernel/random/boot_id').write_text('boot')
            (root/'proc/uptime').write_text('12 0')
            config=root/'config';config.write_text(json.dumps({'daemon_artifact_sha256':'a'*64}))
            def path(value):
                value=str(value)
                return real_path(value) if value==str(config) else root/value.lstrip('/')
            with patch.object(verifier,'Path',side_effect=path),patch.object(verifier,'verify',side_effect=ValueError('integrity rejected')),patch.object(sys,'argv',['verify','--config',str(config),'--context','service_pre']),patch('builtins.print'):
                with self.assertRaisesRegex(ValueError,'integrity rejected'):verifier.main()
            record=json.loads((root/'var/lib/cloud-glider/boot-timing/boot.jsonl').read_text())
            self.assertEqual(record['outcome'],'FAILED');self.assertEqual(record['boot_context'],'service_pre')
    def test_failed_start_helper_reports_without_importing_daemon(self):
        import importlib.util
        import tempfile
        import types
        import io
        from unittest.mock import patch
        spec=importlib.util.spec_from_file_location('failed_boot_helper',Path(__file__).resolve().parents[1]/'ami/files/export_failed_boot.py')
        helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
        real_path=Path;sink=Sink()
        with tempfile.TemporaryDirectory() as directory:
            root=real_path(directory);(root/'proc/sys/kernel/random').mkdir(parents=True)
            (root/'proc/sys/kernel/random/boot_id').write_text('boot')
            (root/'etc/cloud-glider').mkdir(parents=True)
            (root/'etc/cloud-glider/image.json').write_text(json.dumps({'source_commit':'c'*40,'daemon_sha256':'a'*64}))
            config=root/'config';config.write_text(json.dumps(dict(environment='sandbox',request_id='smoke',generation='000000',daemon_operations_log_group='group')))
            def path(value):
                value=str(value);return real_path(value) if value==str(config) else root/value.lstrip('/')
            def request(req,**kw):
                return io.BytesIO(b'{"region":"us-west-2"}' if 'document' in req.full_url else b'i-smoke')
            fake_boto=types.SimpleNamespace(Session=lambda **kw:types.SimpleNamespace(client=lambda *a,**kw:sink))
            with patch.object(helper,'Path',side_effect=path),patch.object(helper.urllib.request,'urlopen',side_effect=request),patch.object(sys,'argv',['helper','--config',str(config)]),patch.dict('os.environ',{'SERVICE_RESULT':'exit-code','INVOCATION_ID':'broken'}),patch.dict(sys.modules,{'timing_export':sys.modules['cloud_glider.timing_export'],'boto3':fake_boto}):
                helper.main()
            self.assertTrue(sink.records[-1]['complete'])
            self.assertIn('InterpreterOrStartupFailure',[r.get('error_code') for r in sink.records])
