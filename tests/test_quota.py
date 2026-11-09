import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
SCRIPTS=Path(__file__).resolve().parents[1]/'skills/task-handoff/scripts'
sys.path.insert(0,str(SCRIPTS))
from quota_monitor import Monitor, normalize, read_limits
from create_handoff import create

def result(primary=50,secondary=50):
    return {'rateLimitsByLimitId':{'codex':{'primary':{'usedPercent':primary,'windowDurationMins':300,'resetsAt':time.time()+3600},'secondary':{'usedPercent':secondary,'windowDurationMins':10080,'resetsAt':time.time()+86400}}}}

class QuotaTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.m=Monitor(self.root/'state')
        self.file=self.root/'checkpoint.md';self.file.write_text('# Example\nSaved work.',encoding='utf-8')
    def tearDown(self):self.m.close();self.tmp.cleanup()
    def test_windows_thresholds_and_unknown(self):
        self.assertEqual(normalize(result(50,88))['level'],'warning')
        self.assertEqual(normalize(result(90,10))['level'],'critical')
        self.assertEqual(normalize({})['level'],'unknown')
        self.assertEqual(normalize(result(),limit_id='other')['level'],'unknown')
        bad=result();bad['rateLimitsByLimitId']['codex']['primary']['usedPercent']=float('nan');bad['rateLimitsByLimitId']['codex']['secondary']['resetsAt']=0
        self.assertEqual(normalize(bad)['level'],'unknown')
    def test_trigger_once_saved_snapshot_and_rearm(self):
        self.m.checkpoint('task',self.file);self.file.unlink()
        a=self.m.sample(reader=lambda _:result(92,10));self.assertEqual(len(a['events']),1)
        handoff=Path(a['events'][0]['path']);self.assertIn('Saved work.',handoff.read_text());self.assertIn('没有停止原 agent',handoff.read_text())
        self.assertEqual(len(self.m.sample(reader=lambda _:result(99,99))['events']),1)
        self.file.write_text('# New version')
        self.assertFalse(self.m.checkpoint('task',self.file)['armed'])
        self.m.checkpoint('task',self.file,arm=True)
        self.assertEqual(len(self.m.sample(reader=lambda _:result(99,99))['events']),2)
    def test_failure_never_handoffs_and_stale_never_healthy(self):
        self.m.checkpoint('task',self.file)
        def fail(_):raise TimeoutError()
        status=self.m.sample(reader=fail);self.assertEqual(status['quota']['level'],'unknown');self.assertFalse(status['events'])
        self.m.sample(reader=lambda _:result())
        with patch('quota_monitor.time.time',return_value=time.time()+400):
            self.assertEqual(self.m.status()['quota']['level'],'unknown');self.assertIsNone(self.m.status()['quota']['remaining_percent'])
    def test_persistence_disarm_and_ack(self):
        self.m.checkpoint('task',self.file);self.m.disarm('task')
        self.assertFalse(self.m.sample(reader=lambda _:result(99,99))['events'])
        self.m.checkpoint('task',self.file,arm=True);s=self.m.sample(reader=lambda _:result(99,99));eid=s['events'][0]['id']
        self.m.delivered(eid);self.m.close();self.m=Monitor(self.root/'state')
        self.assertEqual(self.m.status()['events'][0]['delivered'],1)
    def test_generator_immutable_and_validation(self):
        payload=dict(title='Example',goal='Finish',current='Step 1',next_steps='Step 2',execution='No active process')
        a=create(payload,self.root);b=create(payload,self.root);self.assertNotEqual(a,b)
        with self.assertRaises(ValueError):create({'title':'Missing'},self.root)
        self.file.write_bytes(b'x'*56001)
        with self.assertRaises(ValueError):self.m.checkpoint('task',self.file)

if __name__=='__main__':unittest.main()
