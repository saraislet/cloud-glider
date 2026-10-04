import unittest
from unittest.mock import Mock,patch
from bootstrap.handler import request_terminal_cleanup

class TerminalCleanupTests(unittest.TestCase):
    def setUp(self):
        self.control={'PK':{'S':'CONTROL'},'SK':{'S':'GLOBAL'},'max_generation':{'N':'10'},'command_sequence':{'N':'1'},**{f:{'BOOL':False} for f in ['start_requested','stop_requested','cleanup_requested']}}
        self.current={'PK':{'S':'CURRENT'},'SK':{'S':'GLOBAL'},'generation':{'S':'000010'},'status':{'S':'CURRENT'},'request_id':{'S':'2'},'retirement_completed':{'BOOL':True}}
        self.request={'request_id':{'S':'2'},'propagation_enabled':{'BOOL':True},'cleanup_requested':{'BOOL':False},'cleanup_status':{'S':'IDLE'}}
    def call(self,hold=None,image=None):
        client=Mock()
        with patch('bootstrap.handler.snapshot',return_value=[self.control,self.current,hold or {},self.request]):
            result=request_terminal_cleanup(client,{'STATE_TABLE':'state'},image or self.current.copy())
        return result,client
    def test_terminal_requests_supported_cleanup(self):
        ok,client=self.call();self.assertTrue(ok);self.assertEqual(len(client.transact_write_items.call_args.kwargs['TransactItems']),4)
    def test_hold_stop_retirement_pending_and_duplicate_block(self):
        self.assertFalse(self.call({'PK':{'S':'HOLD'}})[0])
        for item,field,value in [(self.request,'propagation_enabled',{'BOOL':False}),(self.current,'retirement_completed',{'BOOL':False}),(self.request,'cleanup_requested',{'BOOL':True})]:
            old=item[field];item[field]=value;self.assertFalse(self.call()[0]);item[field]=old
    def test_nonterminal_and_stale_cycle_block(self):
        self.current['generation']={'S':'000009'};self.assertFalse(self.call()[0]);self.current['generation']={'S':'000010'}
        stale=self.current.copy();stale['request_id']={'S':'1'};self.assertFalse(self.call(image=stale)[0])
