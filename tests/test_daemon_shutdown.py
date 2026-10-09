import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class ShutdownTests(unittest.TestCase):
    def test_sigterm_unwinds_daemon_and_gateway_then_drains_real_exporter(self):
        root = Path(__file__).resolve().parents[1]
        script = r'''
import json, os, runpy, signal, sys
from pathlib import Path
from types import SimpleNamespace
entry = runpy.run_path(sys.argv[1])
from cloud_glider import timing
from cloud_glider.timing_export import Exporter
destination = Path(sys.argv[2])
class Client:
    def create_log_stream(self, **kw): pass
    def put_log_events(self, **kw):
        with destination.open('a') as output:
            for event in kw['logEvents']: output.write(event['message'] + '\n')
        return {}
class Gateway:
    instance_id = 'i-test'
    region = 'us-west-2'
    def close(self):
        os.kill(os.getpid(), signal.SIGTERM)  # repeated shutdown cannot interrupt drain
        timing.emit('phase_timing', phase='gateway_closed', outcome='PASSED')
class Daemon:
    def __init__(self, *args): pass
    def run(self):
        try:
            for n in range(20): timing.emit('api_timing', operation='describe_instances', outcome='PASSED')
            os.kill(os.getpid(), signal.SIGTERM)
            raise AssertionError('SIGTERM must leave the lifecycle loop')
        finally:
            timing.emit('phase_timing', phase='monitor_closed', outcome='PASSED')
def configure(*args):
    timing._exporter = Exporter(Client(), 'group', 'stream', {'instance_id': 'i-test'})
namespace = entry['main'].__globals__
namespace.update(DaemonConfig=SimpleNamespace(load=lambda path: SimpleNamespace(generation='000000', request_id='1')),
                 AwsSdkGateway=lambda config: Gateway(), Daemon=Daemon, configure_export=configure)
sys.argv = ['cloud-glider', '--config', sys.argv[3]]
entry['main']()
'''
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'timing.jsonl'
            config = Path(directory) / 'config.json'
            config.write_text(json.dumps({'propagation_backend': 'cloudformation'}))
            result = subprocess.run([sys.executable, '-c', script,
                str(root / 'daemon/bin/cloud-glider'), str(output), str(config)],
                env={**os.environ, 'CLOUD_GLIDER_TIMING_EXPORT': '1'},
                capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            records = [json.loads(line) for line in output.read_text().splitlines()]
        marker = records[-1]
        self.assertEqual(marker['event'], 'timing_collection_complete')
        self.assertTrue(marker['complete'])
        self.assertEqual(marker['accepted'], marker['uploaded'])
        self.assertEqual(marker['accepted'], len(records) - 1)
        phases = [r.get('phase') for r in records]
        self.assertLess(phases.index('monitor_closed'), phases.index('gateway_closed'))
        self.assertEqual(sum(r.get('event') == 'api_timing' for r in records), 20)
