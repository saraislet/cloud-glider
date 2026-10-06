#!/usr/bin/env python3
"""Read-only timing receipt validation; duplicates are accepted only if identical."""
import argparse
import hashlib
import json
from pathlib import Path

IDENTITY = ('environment','request_id','generation','instance_id','boot_id','source_commit','daemon_sha256')

def retrieve(client, group, expected):
    """Read every page of each independently expected instance's boot streams."""
    records=[]
    for identity in expected:
        prefix='timing/'+identity['request_id']+'/'+identity['generation']+'/'+identity['instance_id']+'/'
        streams=[]; token=None; seen=set()
        while True:
            kwargs=dict(logGroupName=group,logStreamNamePrefix=prefix)
            if token:kwargs['nextToken']=token
            page=client.describe_log_streams(**kwargs)
            streams.extend(s['logStreamName'] for s in page.get('logStreams',[]))
            token=page.get('nextToken')
            if not token:break
            if token in seen:raise ValueError('LoopingStreamPagination')
            seen.add(token)
        if not streams:raise ValueError('MissingTimingStream:'+identity['instance_id'])
        for stream in sorted(set(streams)):
            token=None; seen=set()
            while True:
                kwargs=dict(logGroupName=group,logStreamName=stream,startFromHead=True)
                if token:kwargs['nextToken']=token
                page=client.get_log_events(**kwargs)
                records.extend(json.loads(e['message']) for e in page.get('events',[]))
                next_token=page.get('nextForwardToken')
                if next_token==token:break
                if not next_token:raise ValueError('MissingEventPaginationToken')
                if next_token in seen:raise ValueError('LoopingEventPagination')
                seen.add(next_token);token=next_token
    return records

def validate(records, *, require_boot=True, expected=None):
    producers={}
    for r in records:
        if any(not isinstance(r.get(k),str) or not r[k] for k in IDENTITY):
            raise ValueError('MissingTimingCorrelation')
        producer=r.get('producer_id')
        if not producer: raise ValueError('MissingProducer')
        state=producers.setdefault(producer,{'records':{},'marker':None,'identity':{k:r[k] for k in IDENTITY}})
        if any(r[k]!=state['identity'][k] for k in IDENTITY): raise ValueError('MixedTimingIdentity')
        if r.get('event')=='timing_collection_complete':
            if state['marker'] is not None and state['marker']!=r: raise ValueError('ConflictingCompletion')
            state['marker']=r;continue
        sequence=r.get('sequence');rid=r.get('record_id')
        if not isinstance(sequence,int) or sequence<1 or rid!=producer+':'+str(sequence): raise ValueError('InvalidRecordIdentity')
        if sequence in state['records'] and state['records'][sequence]!=r: raise ValueError('ConflictingDuplicate')
        state['records'][sequence]=r
    if not producers: raise ValueError('NoTimingRecords')
    for state in producers.values():
        m=state['marker'];rs=state['records']
        if not m or m.get('complete') is not True or m.get('dropped')!=0 or m.get('upload_errors')!=0: raise ValueError('IncompleteTimingCollection')
        if m['accepted']!=m['uploaded'] or set(rs)!=set(range(1,m['accepted']+1)): raise ValueError('MissingTimingRecords')
        h=hashlib.sha256()
        for n in sorted(rs):h.update(json.dumps(rs[n],sort_keys=True,separators=(',',':')).encode()+b'\n')
        if h.hexdigest()!=m['sha256']:raise ValueError('CorruptTimingDigest')
        if require_boot:
            phases=[r.get('phase') for r in rs.values() if r.get('event')=='boot_timing']
            for phase in ('entrypoint_started','runtime_imports','ec2_runtime_imports','sdk_initialization'):
                if phase not in phases:raise ValueError('MissingBootPhase:'+phase)
            contexts={r.get('boot_context') for r in rs.values() if r.get('phase')=='baked_image_verification'}
            if not {'user_data','service_pre'} <= contexts:raise ValueError('MissingVerifierRecords')
    if expected is not None:
        actual={(s['identity']['instance_id'],s['identity']['generation'],s['identity']['request_id'],s['identity']['source_commit'],s['identity']['daemon_sha256']) for s in producers.values()}
        wanted={(e['instance_id'],e['generation'],e['request_id'],e['source_commit'],e['daemon_sha256']) for e in expected}
        if actual!=wanted:raise ValueError('MissingOrUnexpectedTimingInstance')
    return {'producers':len(producers),'records':sum(len(s['records']) for s in producers.values()),'complete':True}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('path',type=Path);p.add_argument('--expected',type=Path,required=True);args=p.parse_args();print(json.dumps(validate(json.loads(args.path.read_text()),expected=json.loads(args.expected.read_text()))))
if __name__=='__main__':main()
