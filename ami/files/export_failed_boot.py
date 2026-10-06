#!/usr/bin/env python3
"""Bounded failed-start diagnostics, independent of the daemon entry interpreter."""
import argparse
import glob
import json
import os
import re
from pathlib import Path
import sys
import urllib.request
from types import SimpleNamespace

# The system interpreter can report a broken venv entry interpreter. Missing
# SDK/library files can still prevent delivery: missing receipts remain failure.
for path in glob.glob('/opt/cloud-glider/venv/lib/python*/site-packages'):
    sys.path.append(path)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--verifier-failed',action='store_true')
    args=parser.parse_args()
    if not args.verifier_failed and os.environ.get('SERVICE_RESULT')=='success':
        return
    config=SimpleNamespace(**json.loads(args.config.read_text()))
    boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    if (Path('/var/lib/cloud-glider') / ('timing-started-'+boot+'-'+os.environ.get('INVOCATION_ID','manual'))).exists():
        return  # Main runtime owns its receipt, including any loss counters.
    os.environ['CLOUD_GLIDER_TIMING_EXPORT']='1'
    # This installed stdlib module is independent of the failed daemon package.
    from timing_export import Exporter
    import boto3
    from botocore.config import Config
    def imds(path):
        token_request=urllib.request.Request('http://169.254.169.254/latest/api/token',
            method='PUT',headers={'X-aws-ec2-metadata-token-ttl-seconds':'60'})
        with urllib.request.urlopen(token_request,timeout=.3) as response: token=response.read().decode()
        request=urllib.request.Request('http://169.254.169.254/latest/'+path,
            headers={'X-aws-ec2-metadata-token':token})
        with urllib.request.urlopen(request,timeout=.3) as response:return response.read().decode()
    instance=imds('meta-data/instance-id')
    region=json.loads(imds('dynamic/instance-identity/document'))['region']
    session=boto3.Session(region_name=region)
    transport=Config(connect_timeout=.3,read_timeout=.3,retries={'total_max_attempts':1})
    ec2=session.client('ec2',config=transport)
    try:
        result=ec2.describe_instances(InstanceIds=[instance])
    finally:
        ec2.close()
    instances=[i for reservation in result.get('Reservations',[]) for i in reservation.get('Instances',[])]
    if len(instances)!=1 or instances[0].get('InstanceId')!=instance:
        raise ValueError('MissingFailedBootIdentity')
    tags={t['Key']:t['Value'] for t in instances[0].get('Tags',[])}
    request_id=tags.get('bootstrap-request-id','')
    generation=tags.get('generation','')
    if not re.fullmatch(r'[1-9][0-9]{0,17}',request_id) or not re.fullmatch(r'[0-9]{6}',generation):
        raise ValueError('InvalidFailedBootCorrelation')
    manifest=json.loads(Path('/etc/cloud-glider/image.json').read_text())
    identity=dict(environment=config.environment,request_id=request_id,
        generation=generation,instance_id=instance,boot_id=boot,
        source_commit=manifest['source_commit'],daemon_sha256=manifest['daemon_sha256'])
    def client():return session.client('logs',config=transport)
    export=Exporter(client,config.daemon_operations_log_group,
        'timing/'+request_id+'/'+generation+'/'+instance+'/'+boot,identity)
    spool=Path('/var/lib/cloud-glider/boot-timing')/(boot+'.jsonl')
    if spool.exists():
        with spool.open() as source:text=source.read(65536)
        for line in text.splitlines():
            record=json.loads(line)
            if record.get('boot_context')=='user_data' or record.get('startup_invocation')==os.environ.get('INVOCATION_ID'):
                export.enqueue(record)
    export.enqueue({'event':'boot_timing','phase':'failed_boot_export','outcome':'FAILED',
        'error_code':'VerifierFailure' if args.verifier_failed else 'InterpreterOrStartupFailure'})
    export.close(.8)


if __name__=='__main__':
    main()
