"""Loopback observer. AWS operations are exclusively Scan and Streams reads."""
import argparse
import datetime as dt
import json
import logging
import signal
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import boto3
from boto3.dynamodb.types import TypeDeserializer
from botocore.config import Config
from botocore.exceptions import ClientError

LOG = logging.getLogger(__name__)
STATIC = Path(__file__).with_name('static')

def iso(epoch=None):
    return dt.datetime.fromtimestamp(time.time() if epoch is None else epoch, dt.timezone.utc).isoformat()

def decode(item):
    decoder = TypeDeserializer()
    return json.loads(json.dumps({k: decoder.deserialize(v) for k, v in item.items()}, default=lambda v: float(v)))

def project(rows, region):
    """Display observations, never infer termination or authoritative readiness."""
    current = next((r for r in rows if r.get('PK') == 'CURRENT'), {})
    request = next((r for r in rows if r.get('PK') == 'BOOTSTRAP'), {})
    hold = any(r.get('PK') == 'HOLD' and r.get('SK') == 'ACTIVE' for r in rows)
    nodes = {}
    for r in rows:
        if not r.get('instance_id') or r.get('generation') is None or not (r.get('PK', '').startswith('GEN#') or r.get('PK') == 'CURRENT'):
            continue
        identity = str(r.get('request_id', 'legacy')) + ':' + r['instance_id']
        old = nodes.get(identity, {})
        nodes[identity] = {**old, **r}
    out = []
    for identity, r in nodes.items():
        status = r.get('status', '')
        owner = current.get('status') == 'CURRENT' and current.get('instance_id') == r['instance_id'] and current.get('request_id') == r.get('request_id')
        state = 'ERROR' if status == 'ERROR' else 'BOOTING'
        if status in ('TERMINATED', 'RETIRED') and r.get('termination_confirmed'):
            state = 'TERMINATED'
        elif status == 'ERROR':
            state = 'ERROR'
        elif owner:
            state = 'READY'
        elif isinstance(r.get('functional_readiness'), dict):
            state = 'READY'
        if hold:
            state = 'EMERGENCY_HOLD'
        event = {'instance_id': identity, 'aws_instance_id': r['instance_id'], 'generation': int(r['generation']),
                 'request_id': str(r.get('request_id', 'legacy')), 'state': state, 'region': region,
                 'timestamp': r.get('updated_at') or iso(), 'ownership_at': current.get('updated_at') if owner else None,
                 'readiness_basis':'accepted CURRENT ownership' if owner else 'reported functional proof' if r.get('functional_readiness') else 'no authoritative readiness evidence',
                 'functional_readiness': r.get('functional_readiness'), 'source_updated_at':r.get('updated_at'), 'workload_healthy': r.get('workload_healthy'),
                 'propagation_enabled': request.get('propagation_enabled', False), 'raw_status': status}
        if r.get('heartbeat_at_epoch') is not None:
            event['heartbeat_at'] = iso(float(r['heartbeat_at_epoch']))
        parent = r.get('predecessor_instance_id')
        if parent and parent != 'NONE':
            event['parent_id'] = event['request_id'] + ':' + parent
        out.append(event)
    by_id = {e['instance_id']: e for e in out}
    for e in out:
        e['child_ids'] = [c['instance_id'] for c in out if c.get('parent_id') == e['instance_id']]
        if e.get('parent_id') in by_id:
            by_id[e['parent_id']]['cleanup_owner_id'] = e['instance_id']
        if (e.get('parent_id') and current.get('status')=='CURRENT'
                and current.get('request_id')==e.get('request_id')
                and current.get('instance_id') == e['aws_instance_id']):
            parent = by_id.get(e['parent_id'])
            if parent and current.get('retirement_authorized') and not hold:
                parent['state'] = 'TERMINATED' if current.get('retirement_completed') else 'DRAINING'
    physical = {r.get('instance_id'): r.get('ec2_state') for r in rows if r.get('PK') == 'EC2'}
    for e in out:
        e['ec2_state'] = physical.get(e['aws_instance_id'])
        if e['ec2_state'] == 'terminated': e['state'] = 'TERMINATED'
        elif e['ec2_state'] == 'shutting-down': e['state'] = 'DRAINING'
    return out

class Store:
    def __init__(self, path):
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.executescript('''PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS records(source TEXT, pk TEXT, sk TEXT, data TEXT, PRIMARY KEY(source,pk,sk));
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, uid TEXT UNIQUE, at REAL, data TEXT);
        CREATE TABLE IF NOT EXISTS checkpoints(stream TEXT, shard TEXT, sequence TEXT, done INTEGER DEFAULT 0, PRIMARY KEY(stream,shard));
        CREATE TABLE IF NOT EXISTS raw_events(uid TEXT PRIMARY KEY, data TEXT);
        CREATE TABLE IF NOT EXISTS nodes(id TEXT PRIMARY KEY, data TEXT);
        ''')
    def checkpoint(self, arn, shard):
        with self.lock:
            return self.db.execute('SELECT sequence,done FROM checkpoints WHERE stream=? AND shard=?', (arn,shard)).fetchone()
    def update(self, source, changes, region, uid, raw=None, checkpoint=None, replace=False):
        with self.lock, self.db:
            if raw is not None:
                inserted=self.db.execute('INSERT OR IGNORE INTO raw_events VALUES (?,?)', (uid,json.dumps(raw,default=str)))
                if not inserted.rowcount:
                    return  # A committed record and its checkpoint must never be replayed over newer state.
            if replace:
                self.db.execute('DELETE FROM records WHERE source=?', (source,))
            for keys, value in changes:
                pk, sk = keys['PK'], keys['SK']
                if value is None:
                    self.db.execute('DELETE FROM records WHERE source=? AND pk=? AND sk=?',(source,pk,sk))
                else:
                    self.db.execute('INSERT OR REPLACE INTO records VALUES (?,?,?,?)',(source,pk,sk,json.dumps(value)))
            rows = [json.loads(r[0]) for r in self.db.execute('SELECT data FROM records')]
            prior = {r[0]:json.loads(r[1]) for r in self.db.execute('SELECT id,data FROM nodes')}
            for e in project(rows,region):
                old = prior.get(e['instance_id'])
                if old and old['state'] == 'TERMINATED':
                    e['state'] = 'TERMINATED'  # Exact instance identities never become live again.
                comparable = {k:v for k,v in e.items() if k != 'timestamp'}
                if old and comparable == {k:v for k,v in old.items() if k not in ('timestamp','event_id','created_at','ready_at','terminated_at','history')}:
                    continue
                e['timestamp'] = iso()  # observation clock; source times remain separately available
                e['event_id'] = uid + ':' + e['instance_id']
                old=old or {}
                e['created_at']=old.get('created_at',e['timestamp'])
                for field in ('ready_at','terminated_at'):
                    if field in old: e[field]=old[field]
                if e['state']=='READY' and 'ready_at' not in e: e['ready_at']=e['timestamp']
                if e['state']=='TERMINATED' and 'terminated_at' not in e: e['terminated_at']=e['timestamp']
                e['history']=list(old.get('history',[]))
                if not e['history'] or e['history'][-1]['state']!=e['state']:
                    e['history'].append({'state':e['state'],'timestamp':e['timestamp']})
                self.db.execute('INSERT OR IGNORE INTO events(uid,at,data) VALUES (?,?,?)',(e['event_id'],time.time(),json.dumps(e)))
                self.db.execute('INSERT OR REPLACE INTO nodes VALUES (?,?)',(e['instance_id'],json.dumps(e)))
            # Deleted DynamoDB records remain in history, with explicitly unknown physical state.
            present = {e['instance_id'] for e in project(rows, region)}
            for identity, old in prior.items():
                if identity not in present and not old.get('record_removed'):
                    e = {**old, 'record_removed':True, 'state':old['state'] if old['state']=='TERMINATED' else 'WAITING', 'timestamp':iso(), 'event_id':uid+':removed:'+identity}
                    self.db.execute('INSERT OR IGNORE INTO events(uid,at,data) VALUES (?,?,?)',(e['event_id'],time.time(),json.dumps(e)))
                    self.db.execute('UPDATE nodes SET data=? WHERE id=?',(json.dumps(e),identity))
            if checkpoint:
                self.db.execute('INSERT OR REPLACE INTO checkpoints VALUES (?,?,?,?)',checkpoint)
    def snapshot(self):
        with self.lock:
            cursor = self.db.execute('SELECT COALESCE(MAX(id),0) FROM events').fetchone()[0]
            nodes = [json.loads(r[0]) for r in self.db.execute('SELECT data FROM nodes')]
            records = [json.loads(r[0]) for r in self.db.execute("SELECT data FROM records WHERE pk IN ('CONTROL','CURRENT','BOOTSTRAP','HOLD')")]
            return {'instances':nodes,'cursor':cursor,'controls':records}
    def events(self, after=0):
        with self.lock:
            return [(r[0],json.loads(r[1])) for r in self.db.execute('SELECT id,data FROM events WHERE id>? ORDER BY id LIMIT 1000',(after,))]

class Observer:
    def __init__(self, store, profile, region, tables):
        self.store, self.region, self.tables = store, region, tables
        session = boto3.Session(profile_name=profile, region_name=region)
        config = Config(connect_timeout=5,read_timeout=10,retries={'max_attempts':3})
        self.ddb = session.client('dynamodb',config=config)
        self.streams = session.client('dynamodbstreams',config=config)
        self.ec2 = session.client('ec2',config=config)
        self.stop = threading.Event()
        self.active = threading.Event()
        self.collection_lock = threading.RLock()
        self.status = {'state':'paused','tables':{},'warnings':[]}
        self.shards = {}
        self.watermarks = {}
    def scan(self, table):
        start = time.time()
        rows = []
        for page in self.ddb.get_paginator('scan').paginate(TableName=table,ConsistentRead=True):
            rows.extend(decode(i) for i in page['Items'])
        self.store.update(table,[(r,r) for r in rows],self.region,'scan:'+str(time.time_ns()),
                          raw={'source':table,'observed_at':iso(),'snapshot':rows},replace=True)
        self.watermarks[table] = start
    def discover(self, table, arn):
        shards, args = [], {'StreamArn':arn}
        while True:
            desc = self.streams.describe_stream(**args)['StreamDescription']
            shards.extend(desc.get('Shards',[]))
            if not desc.get('LastEvaluatedShardId'): break
            args['ExclusiveStartShardId'] = desc['LastEvaluatedShardId']
        for shard in shards:
            sid = shard['ShardId']; key = (arn,sid)
            if key in self.shards: continue
            checkpoint = self.store.checkpoint(arn,sid)
            if checkpoint and checkpoint[1]: continue
            parent = shard.get('ParentShardId')
            if parent and any(s['ShardId']==parent for s in shards):
                cp = self.store.checkpoint(arn,parent)
                if not cp or not cp[1]: continue
            args = {'StreamArn':arn,'ShardId':sid,'ShardIteratorType':'AFTER_SEQUENCE_NUMBER' if checkpoint and checkpoint[0] else 'TRIM_HORIZON'}
            if checkpoint and checkpoint[0]: args['SequenceNumber'] = checkpoint[0]
            try:
                iterator = self.streams.get_shard_iterator(**args)['ShardIterator']
            except ClientError as exc:
                if exc.response['Error']['Code'] != 'TrimmedDataAccessException': raise
                self.status['warnings'].append('Stream checkpoint expired: history has a gap; resuming oldest retained records.')
                args.pop('SequenceNumber',None);args['ShardIteratorType']='TRIM_HORIZON'
                iterator = self.streams.get_shard_iterator(**args)['ShardIterator']
            self.shards[key] = (table,iterator)
    def poll(self):
        for (arn,sid),(table,iterator) in list(self.shards.items()):
            try:
                response = self.streams.get_records(ShardIterator=iterator,Limit=100)
            except ClientError as exc:
                if exc.response['Error']['Code']=='AccessDeniedException':
                    self.status['tables'][table]='snapshot polling (stream access denied)'
                    warning='Stream read permission missing: GetShardIterator/GetRecords; no IAM changes made.'
                    if warning not in self.status['warnings']: self.status['warnings'].append(warning)
                    del self.shards[(arn,sid)];continue
                if exc.response['Error']['Code'] in ('ExpiredIteratorException','TrimmedDataAccessException'):
                    if exc.response['Error']['Code']=='TrimmedDataAccessException':
                        self.status['warnings'].append('Stream records expired; retained history may have a gap.')
                        with self.store.lock,self.store.db:
                            self.store.db.execute('DELETE FROM checkpoints WHERE stream=? AND shard=?',(arn,sid))
                    del self.shards[(arn,sid)];continue
                raise
            if not any('stream access denied' in mode for mode in self.status['tables'].values()):
                self.status['warnings']=[w for w in self.status['warnings'] if not w.startswith('Stream read permission missing:')]
            records=response.get('Records',[])
            for record in records:
                data=record['dynamodb'];at=data.get('ApproximateCreationDateTime')
                # Archive pre-snapshot changes without letting them regress current state.
                fresh = at is None or at.timestamp() >= self.watermarks.get(table,0)
                keys=decode(data['Keys']);image=decode(data['NewImage']) if 'NewImage' in data else None
                self.store.update(table,[(keys,image)] if fresh else [],self.region,arn+':'+record['eventID'],raw=record,
                                  checkpoint=(arn,sid,data['SequenceNumber'],0))
            next_iterator=response.get('NextShardIterator')
            if not records and self.store.checkpoint(arn,sid) is None:
                with self.store.lock,self.store.db:
                    self.store.db.execute('INSERT OR IGNORE INTO checkpoints VALUES (?,?,NULL,0)',(arn,sid))
            if next_iterator:
                self.shards[(arn,sid)]=(table,next_iterator)
            else:
                cp=self.store.checkpoint(arn,sid)
                with self.store.lock,self.store.db:
                    self.store.db.execute('INSERT OR REPLACE INTO checkpoints VALUES (?,?,?,1)',(arn,sid,cp[0] if cp else None))
                del self.shards[(arn,sid)]
    def reconcile_instances(self):
        with self.store.lock:
            rows = [json.loads(r[0]) for r in self.store.db.execute('SELECT data FROM records')]
        ids = sorted({r['instance_id'] for r in rows if r.get('instance_id') and r.get('generation') is not None})
        if not ids: return
        changes = []
        for offset in range(0,len(ids),100):
            response = self.ec2.describe_instances(InstanceIds=ids[offset:offset+100])
            for reservation in response['Reservations']:
                for instance in reservation['Instances']:
                    row = {'PK':'EC2','SK':instance['InstanceId'],'instance_id':instance['InstanceId'],
                           'ec2_state':instance['State']['Name']}
                    changes.append((row,row))
        self.store.update('ec2',changes,self.region,'ec2:'+str(time.time_ns()))
    def set_active(self, enabled):
        # Stop waits for an in-flight collection pass; after return no AWS read can start.
        with self.collection_lock:
            if enabled:
                self.active.set();self.status.update(state='starting',error=None)
            else:
                self.active.clear();self.status.update(state='paused',error=None)
        return {'collection_active':self.active.is_set(),'state':self.status['state']}
    def run(self):
        last_scan,last_discover,last_ec2 = {},0,0
        while not self.stop.is_set():
            with self.collection_lock:
                if not self.active.is_set():
                    last_scan,last_discover,last_ec2 = {},0,0
                    self.shards.clear()
                else:
                    try:
                        now=time.time()
                        if now-last_discover>30:
                            for table in self.tables:
                                desc=self.ddb.describe_table(TableName=table)['Table']
                                arn=desc.get('LatestStreamArn')
                                self.status['tables'][table]='stream + reconciliation' if arn else 'snapshot polling (stream disabled)'
                                if arn:
                                    try: self.discover(table,arn)
                                    except ClientError as exc:
                                        if exc.response['Error']['Code'] not in ('AccessDeniedException','UnauthorizedOperation'): raise
                                        self.status['tables'][table]='snapshot polling (stream access denied)'
                                        warning='Stream read permission missing: GetShardIterator/GetRecords; no IAM changes made.'
                                        if warning not in self.status['warnings']: self.status['warnings'].append(warning)
                            last_discover=now
                        for table in self.tables:
                            interval=30 if self.status['tables'].get(table,'').startswith('stream') else 5
                            if now-last_scan.get(table,0)>interval:
                                self.scan(table);last_scan[table]=now
                        self.poll()
                        if now-last_ec2 > 5:
                            self.reconcile_instances();last_ec2=now
                        self.status.update(state='connected',last_success=iso(),error=None)
                    except Exception as exc:
                        LOG.warning('Observer read failed: %s',exc.response['Error']['Code'] if isinstance(exc,ClientError) else type(exc).__name__)
                        self.status.update(state='unavailable',error=type(exc).__name__)
                        self.stop.wait(5)
            self.stop.wait(1)

class Server(ThreadingHTTPServer):
    daemon_threads=True
    allow_reuse_address=True

class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if (self.headers.get('Host') not in ('127.0.0.1:8000','localhost:8000')
                or self.headers.get('Origin') not in ('http://127.0.0.1:8000','http://localhost:8000')
                or self.headers.get('Content-Length','0') != '0'):
            self.send_error(403);return
        action={'/api/observer/start':True,'/api/observer/stop':False}.get(self.path)
        if action is None: self.send_error(404);return
        return self.respond(self.server.observer.set_active(action))
    def do_GET(self):
        if self.headers.get('Host') not in ('127.0.0.1:8000','localhost:8000'):
            self.send_error(403);return
        origin=self.headers.get('Origin')
        if origin and origin not in ('http://127.0.0.1:8000','http://localhost:8000'):
            self.send_error(403);return
        url=urlparse(self.path)
        if url.path=='/api/status':
            controls=self.server.store.snapshot()['controls']
            request=next((r for r in controls if r.get('PK')=='BOOTSTRAP'),{})
            current=next((r for r in controls if r.get('PK')=='CURRENT'),{})
            return self.respond({**self.server.observer.status,'collection_active':getattr(self.server.observer,'active',threading.Event()).is_set(),'propagation_enabled':request.get('propagation_enabled'),
                                 'hold_active':any(r.get('PK')=='HOLD' for r in controls),'current_status':current.get('status')})
        if url.path=='/api/replay':
            try: after=max(0,int(parse_qs(url.query).get('after',['0'])[0]))
            except ValueError: self.send_error(400);return
            batch=self.server.store.events(after)
            return self.respond({'events':[e for _,e in batch],'next':(batch or [(after,None)])[-1][0]})
        if url.path=='/events':
            self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Cache-Control','no-cache');self.end_headers()
            try:
                snap=self.server.store.snapshot();cursor=snap['cursor']
                # Snapshot is atomic with cursor. Reconnect uses a fresh complete snapshot.
                self.wfile.write(('id: '+str(cursor)+'\ndata: '+json.dumps(snap)+'\n\n').encode());self.wfile.flush()
                while not self.server.observer.stop.wait(1):
                    rows=self.server.store.events(cursor)
                    for cursor,event in rows:
                        self.wfile.write(('id: '+str(cursor)+'\ndata: '+json.dumps(event)+'\n\n').encode())
                    self.wfile.write(b': keepalive\n\n');self.wfile.flush()
            except (BrokenPipeError,ConnectionResetError): pass
            return
        name={'/':'index.html','/app.js':'app.js','/style.css':'style.css'}.get(url.path)
        if not name: self.send_error(404);return
        content=(STATIC/name).read_bytes()
        self.send_response(200);self.send_header('Content-Type',{'index.html':'text/html','app.js':'text/javascript','style.css':'text/css'}[name]);self.send_header('Content-Length',str(len(content)));self.end_headers();self.wfile.write(content)
    def respond(self, data):
        content=json.dumps(data).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(content)
    def log_message(self,*args): pass

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile',default='default');parser.add_argument('--region',default='us-west-2')
    parser.add_argument('--state-table',default='cloud-glider-sandbox-state');parser.add_argument('--generation-table',default='cloud-glider-sandbox-generations')
    parser.add_argument('--database',default='.observer/events.sqlite3')
    args=parser.parse_args();Path(args.database).parent.mkdir(parents=True,exist_ok=True)
    store=Store(args.database);observer=Observer(store,args.profile,args.region,[args.state_table,args.generation_table])
    server=Server(('127.0.0.1',8000),Handler);server.store=store;server.observer=observer
    threading.Thread(target=observer.run,daemon=True).start()
    def stop(*_): observer.stop.set();threading.Thread(target=server.shutdown,daemon=True).start()
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    print('Cloud Glider Observer: http://127.0.0.1:8000',flush=True)
    try: server.serve_forever()
    finally: observer.stop.set();server.server_close()

if __name__=='__main__': main()
