"""Optional outbox bridge. Requires an existing Learning Terminal transfer endpoint."""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile

script=Path(__file__).resolve().parents[1]/'skills/task-handoff/scripts/quota_monitor.py'
spec=importlib.util.spec_from_file_location('quota_monitor',script)
quota=importlib.util.module_from_spec(spec);spec.loader.exec_module(quota)

def deliver(monitor,client):
    count=0
    for event in monitor.status()['events']:
        if event['delivered']:continue
        with tempfile.TemporaryDirectory() as temp:
            request=Path(temp)/'request.json'
            request.write_text(json.dumps(dict(action='register',path=event['path'],reason='额度不足，检查点转交'),ensure_ascii=False),encoding='utf-8')
            result=subprocess.run([sys.executable,str(Path(client).resolve()),'transfer','--file',str(request)],capture_output=True,text=True,timeout=40)
            if result.returncode:raise RuntimeError('登记失败，事件保留待重试；请检查工作台服务。')
            receipt=json.loads(result.stdout)
            if not receipt.get('task',{}).get('id'):raise RuntimeError('工作台未返回有效登记回执')
            monitor.delivered(event['id']);count+=1
    return count

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir',required=True);parser.add_argument('--client',required=True)
    args=parser.parse_args();monitor=quota.Monitor(args.state_dir)
    try:print(json.dumps({'registered':deliver(monitor,args.client)}))
    finally:monitor.close()
