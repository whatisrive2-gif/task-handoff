"""Create an immutable portable Markdown handoff using only Python's standard library."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import uuid

SECTIONS = [('goal','目标与完成标准'),('constraints','要求与授权范围'),('completed','已完成工作与证据'),('current','当前停点'),('artifacts','成果与材料位置'),('decisions','关键判断与依据'),('open_issues','未解决问题与失败尝试'),('next_steps','接续步骤'),('execution','中断时执行状态')]

def create(data, directory):
    title=data.get('title')
    if not isinstance(title,str) or not title.strip() or '\n' in title or len(title)>200: raise ValueError('title 须为单行非空标题，最多 200 字')
    for key in ['reason','task_id',*[k for k,_ in SECTIONS]]:
        if key in data and not isinstance(data[key],str): raise ValueError(key+' 须为文本')
    for key in ('goal','current','next_steps','execution'):
        if not data.get(key,'').strip(): raise ValueError(key+' 必填；不确定时明确填写未知')
    stamp=datetime.now(timezone.utc).isoformat()
    tid=data.get('task_id') or str(uuid.uuid4())
    text=f'# {title}\n\n格式：task-handoff/1\n任务标识：{tid}\n更新时间：{stamp}\n交接原因：{data.get("reason","主动转交")}\n状态：待派发\n'
    for key,label in SECTIONS: text+=f'\n## {label}\n\n{data.get(key) or "未提供，接续时核查。"}\n'
    directory=Path(directory).resolve();directory.mkdir(parents=True,exist_ok=True)
    output=directory/('handoff-'+uuid.uuid4().hex+'.md')
    with output.open('x',encoding='utf-8') as stream: stream.write(text)
    return output

if __name__=='__main__':
    parser=argparse.ArgumentParser(description='JSON 字段：title、goal、current、next_steps、execution 必填；reason、task_id、constraints、completed、artifacts、decisions、open_issues 可选。所有字段为文本。不会调用模型或派发任务。')
    parser.add_argument('--input',required=True);parser.add_argument('--output-dir',required=True)
    args=parser.parse_args()
    print(create(json.loads(Path(args.input).read_text(encoding='utf-8')),args.output_dir))
