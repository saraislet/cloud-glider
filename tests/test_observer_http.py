"""Exercise actual HTTP/SSE delivery against a temporary local store; no AWS calls."""
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from observer.server import Store, Server, Handler

class ObserverHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.tmp.name)/'events.sqlite')
        self.observer=SimpleNamespace(stop=threading.Event(),status={'state':'connected','tables':{},'warnings':[]})
        self.server=Server(('127.0.0.1',0),Handler)
        self.server.store=self.store;self.server.observer=self.observer
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
    def tearDown(self):
        self.observer.stop.set();self.server.shutdown();self.server.server_close();self.thread.join()
        self.store.db.close();self.tmp.cleanup()
    def get(self,path,extra=None):
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=5)
        connection.request('GET',path,headers={'Host':'127.0.0.1:8000',**(extra or {})})
        return connection,connection.getresponse()
    def row(self,heartbeat):
        return {'PK':'GEN#000000','SK':'STATE','generation':'0','instance_id':'i-fixture','request_id':'fixture','status':'CANDIDATE','heartbeat_at_epoch':heartbeat}
    def test_sse_initial_snapshot_update_and_reconnect(self):
        row=self.row(100);self.store.update('t',[(row,row)],'r','first')
        conn,response=self.get('/events')
        self.assertEqual(response.getheader('Content-Type'),'text/event-stream')
        first_id=response.readline().decode().strip()
        snapshot=json.loads(response.readline().decode().removeprefix('data: '))
        self.assertEqual(len(snapshot['instances']),1);response.readline()
        row=self.row(200);self.store.update('t',[(row,row)],'r','second')
        line=response.readline()
        while line.startswith(b':') or line==b'\n': line=response.readline()
        self.assertTrue(line.startswith(b'id: '))
        update=json.loads(response.readline().decode().removeprefix('data: '))
        self.assertEqual(update['created_at'],snapshot['instances'][0]['created_at'])
        self.assertEqual(update['heartbeat_at'],'1970-01-01T00:03:20+00:00')
        conn.close()
        conn,response=self.get('/events',{'Last-Event-ID':first_id.removeprefix('id: ')})
        response.readline();reconnect=json.loads(response.readline().decode().removeprefix('data: '));conn.close()
        self.assertEqual(reconnect['instances'][0]['created_at'],update['created_at'])
        self.assertEqual(reconnect['cursor'],2)
    def test_http_origin_host_and_invalid_cursor(self):
        for path,headers,code in [('/api/status',{'Host':'attacker.example'},403),('/api/status',{'Origin':'https://attacker.example'},403),('/api/replay?after=bad',{},400),('/missing',{},404)]:
            with self.subTest(path=path,headers=headers):
                conn,response=self.get(path,headers);self.assertEqual(response.status,code);response.read();conn.close()
    def test_paginated_replay_preserves_all_events(self):
        # Populate a long independent recording, without starting the AWS observer.
        with self.store.lock,self.store.db:
            self.store.db.executemany('INSERT INTO events(uid,at,data) VALUES (?,?,?)',[(str(i),i,json.dumps({'event_id':str(i)})) for i in range(1005)])
        conn,response=self.get('/api/replay');first=json.loads(response.read());conn.close()
        self.assertEqual(len(first['events']),1000)
        conn,response=self.get('/api/replay?after='+str(first['next']));second=json.loads(response.read());conn.close()
        self.assertEqual(len(second['events']),5)
        self.assertEqual(len({e['event_id'] for e in first['events']+second['events']}),1005)

if __name__=='__main__':unittest.main()
