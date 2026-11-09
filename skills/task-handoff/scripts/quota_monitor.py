"""Codex quota adapter and checkpoint monitor. No model calls or agent dispatch."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import sqlite3
import subprocess
import threading
import time
import uuid


def timestamp(): return datetime.now(timezone.utc).isoformat()


def read_limits(binary='codex', timeout=25):
    """Own a short-lived stdio RPC connection; never log auth or raw errors."""
    proc = subprocess.Popen([binary, 'app-server', '--stdio'], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    messages = queue.Queue()
    def reader():
        for line in proc.stdout:
            try: messages.put(json.loads(line))
            except ValueError: pass
        messages.put(None)
    worker=threading.Thread(target=reader, daemon=True);worker.start()
    deadline=time.monotonic()+timeout
    def send(value):
        proc.stdin.write(json.dumps(value)+'\n');proc.stdin.flush()
    def receive(rid):
        while True:
            remaining=deadline-time.monotonic()
            if remaining<=0: raise TimeoutError('quota_timeout')
            try: message=messages.get(timeout=remaining)
            except queue.Empty: raise TimeoutError('quota_timeout') from None
            if message is None: raise RuntimeError('quota_connection_closed')
            if message.get('id')==rid:
                if 'error' in message: raise RuntimeError('quota_rpc_error')
                return message.get('result',{})
    try:
        send({'id':0,'method':'initialize','params':{'clientInfo':{'name':'task_handoff','version':'0.2.0'}}})
        receive(0);send({'method':'initialized'})
        send({'id':1,'method':'account/rateLimits/read'})
        return receive(1)
    finally:
        proc.terminate()
        try: proc.wait(timeout=3)
        except subprocess.TimeoutExpired: proc.kill();proc.wait()
        worker.join(timeout=1)
        for stream in (proc.stdin,proc.stdout): stream.close()


def normalize(result, warning=20, critical=10, limit_id='codex', at=None):
    at=time.time() if at is None else at
    if not 0<=critical<warning<=100: raise ValueError('thresholds_invalid')
    buckets=result.get('rateLimitsByLimitId') or {}
    bucket=buckets.get(limit_id)
    if bucket is None:
        legacy=result.get('rateLimits') or {}
        if legacy.get('limitId', 'codex')==limit_id: bucket=legacy
    windows=[]
    for name in ('primary','secondary'):
        value=(bucket or {}).get(name)
        if not isinstance(value,dict): continue
        used,minutes,reset=(value.get(k) for k in ('usedPercent','windowDurationMins','resetsAt'))
        if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in (used,minutes,reset)): continue
        if not 0<=used<=100 or minutes<=0 or reset<=at: continue
        windows.append(dict(window=name,window_minutes=minutes,remaining_percent=round(100-used,2),resets_at=reset))
    minimum=min((w['remaining_percent'] for w in windows),default=None)
    level='unknown' if minimum is None else 'critical' if minimum<=critical else 'warning' if minimum<=warning else 'normal'
    return dict(schema='task-handoff-quota/1',provider='codex',limit_id=limit_id,observed_at=timestamp(),
                observed_unix=at,level=level,remaining_percent=minimum,windows=windows,
                warning_percent=warning,critical_percent=critical)


class Monitor:
    def __init__(self,directory):
        self.directory=Path(directory).expanduser().resolve();self.directory.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(self.directory/'monitor.sqlite3',timeout=15)
        self.db.row_factory=sqlite3.Row
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS checkpoints(task_key TEXT PRIMARY KEY,path TEXT,body TEXT,digest TEXT,updated TEXT,armed INTEGER);
          CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,task_key TEXT,path TEXT,digest TEXT,created TEXT,delivered INTEGER DEFAULT 0);
          CREATE TABLE IF NOT EXISTS state(id TEXT PRIMARY KEY,data TEXT);
        ''')
    def close(self): self.db.close()
    def checkpoint(self,key,path,arm=False):
        if not isinstance(key,str) or not key.strip() or len(key)>200: raise ValueError('task_key_invalid')
        path=Path(path).expanduser().resolve()
        if path.suffix.lower()!='.md': raise ValueError('checkpoint_requires_markdown')
        with path.open('rb') as stream: raw=stream.read(56001)
        if len(raw)>56000 or not raw.strip(): raise ValueError('checkpoint_size_invalid')
        body=raw.decode('utf-8-sig');digest=hashlib.sha256(raw).hexdigest()
        with self.db:
            prior=self.db.execute('SELECT armed FROM checkpoints WHERE task_key=?',(key,)).fetchone()
            armed=1 if arm else prior['armed'] if prior else 1
            self.db.execute('INSERT OR REPLACE INTO checkpoints VALUES (?,?,?,?,?,?)',(key,str(path),body,digest,timestamp(),armed))
        return dict(task_key=key,path=str(path),armed=bool(armed))
    def disarm(self,key):
        with self.db:self.db.execute('UPDATE checkpoints SET armed=0 WHERE task_key=?',(key,))
    def sample(self,binary='codex',warning=20,critical=10,limit_id='codex',reader=read_limits):
        try: status=normalize(reader(binary),warning,critical,limit_id)
        except (OSError,ValueError,RuntimeError,TimeoutError):
            status=dict(schema='task-handoff-quota/1',provider='codex',level='unknown',observed_at=timestamp(),observed_unix=time.time(),windows=[],remaining_percent=None,error='额度读取失败；请检查 CLI 登录、网络及版本。')
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO state VALUES (?,?)',('latest',json.dumps(status,ensure_ascii=False)))
            if status['level']=='critical':
                for row in self.db.execute('SELECT * FROM checkpoints WHERE armed=1').fetchall():
                    event_id=uuid.uuid4().hex
                    out=self.directory/'handoffs';out.mkdir(exist_ok=True)
                    dest=out/(event_id+'.md')
                    # Preserve the saved checkpoint and explicitly mark its cutoff.
                    text=row['body']+'\n\n## 额度监测转交记录\n\n'+f'触发时间：{status["observed_at"]}\n\n检查点截止：{row["updated"]}\n\n剩余额度：{status["remaining_percent"]}%\n\n仅保存截至检查点的进度；此后操作需核查。监测器没有停止原 agent，也没有派发给接收方。\n'
                    with dest.open('x',encoding='utf-8') as stream:stream.write(text)
                    self.db.execute('INSERT INTO events VALUES (?,?,?,?,?,0)',(event_id,row['task_key'],str(dest),row['digest'],timestamp()))
                    self.db.execute('UPDATE checkpoints SET armed=0 WHERE task_key=?',(row['task_key'],))
        return self.status()
    def status(self):
        row=self.db.execute('SELECT data FROM state WHERE id=?',('latest',)).fetchone()
        status=json.loads(row[0]) if row else dict(level='unknown',remaining_percent=None,windows=[])
        if time.time()-status.get('observed_unix',0)>300:
            status={**status,'level':'unknown','stale':True,'remaining_percent':None}
        return dict(quota=status,checkpoints=[dict(r) for r in self.db.execute('SELECT task_key,path,updated,armed FROM checkpoints')],events=[dict(r) for r in self.db.execute('SELECT * FROM events ORDER BY created')])
    def delivered(self,event_id):
        with self.db:self.db.execute('UPDATE events SET delivered=1 WHERE id=?',(event_id,))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir',default=os.environ.get('TASK_HANDOFF_STATE',str(Path.home()/'.local/state/task-handoff')))
    sub=parser.add_subparsers(dest='command',required=True)
    for cmd in ('once','watch'):
        p=sub.add_parser(cmd);p.add_argument('--codex',default=os.environ.get('TABER_CODEX_BINARY','codex'));p.add_argument('--warning',type=float,default=20);p.add_argument('--critical',type=float,default=10);p.add_argument('--limit-id',default='codex')
        if cmd=='watch':p.add_argument('--interval',type=int,default=120)
    sub.add_parser('status')
    p=sub.add_parser('checkpoint');p.add_argument('--task-key',required=True);p.add_argument('--path',required=True);p.add_argument('--arm',action='store_true')
    p=sub.add_parser('disarm');p.add_argument('--task-key',required=True)
    args=parser.parse_args();monitor=Monitor(args.state_dir)
    try:
        if args.command in ('once','watch'):
            if not 0<=args.critical<args.warning<=100:parser.error('需要 0 <= critical < warning <= 100')
            if args.command=='watch' and args.interval<30:parser.error('interval 至少 30 秒')
            while True:
                print(json.dumps(monitor.sample(args.codex,args.warning,args.critical,args.limit_id),ensure_ascii=False),flush=True)
                if args.command=='once':break
                time.sleep(args.interval)
        elif args.command=='checkpoint':print(json.dumps(monitor.checkpoint(args.task_key,args.path,args.arm),ensure_ascii=False))
        elif args.command=='disarm':monitor.disarm(args.task_key)
        else:print(json.dumps(monitor.status(),ensure_ascii=False))
    finally:monitor.close()

if __name__=='__main__':main()
