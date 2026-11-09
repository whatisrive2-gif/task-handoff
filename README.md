# task-handoff

把中断任务整理为可独立阅读的 Markdown 交接文件，并用本地 Codex 额度监测提前保存检查点。支持用户主动转交、模型能力不匹配和低额度场景。接收 agent 由用户选择。

## 包含什么

- `skills/task-handoff/`：可安装的通用 agent skill，以及 Python 标准库脚本。
- `quota_monitor.py`：读取 Codex App Server 的实际额度窗口、剩余比例和重置时间；默认每 120 秒检查，20% 预警，10% 生成交接文件。
- 本地检查点与事件记录：即使模型突然无法继续运行，也能保存最近检查点。触发一次后停止监测该任务，避免重复转交。
- `integrations/learning_terminal.py`：可选学习终端登记桥接，仅登记待派发任务。

监测器不调用模型，不自动派发，不终止原 agent。它无法从任意聊天中自动提取最新进度；agent 或宿主需要在任务开始和重要阶段保存检查点。额度通知不会自行注入已经运行的桌面聊天；宿主应在阶段边界查询状态并调用 skill。未提供额度接口的平台暂未适配。

## 安装

需要 Python 3.10+。额度监测还需要已登录的 Codex CLI，且版本支持 `account/rateLimits/read`。

将 `skills/task-handoff` 复制或链接到你的 agent 技能目录，例如 `~/.codex/skills/task-handoff`。重新加载技能后可以说：“请把当前任务交接给下一个 agent。”生成文件不需要学习终端或网络。

## 生成交接文件

创建 `request.json`，以下是虚构示例：

```json
{
  "title": "继续核查示例计算",
  "task_id": "example-task",
  "reason": "主动转交",
  "goal": "完成计算并验证边界条件",
  "current": "已完成第一步",
  "next_steps": "检查第二步的边界条件",
  "execution": "没有运行中的命令"
}
```

```sh
python skills/task-handoff/scripts/create_handoff.py --input request.json --output-dir /absolute/private/handoffs
```

更多字段见脚本 `--help`。每次生成独立新文件，保留旧版本。文件记录目标、约束、成果、依据、失败尝试、停点和下一步。同机引用绝对路径；跨设备时自行携带所需材料并改用相对路径。

## 查询和监测额度

下面的状态目录应位于代码仓库之外。命令使用当前 Codex CLI 的登录账号，可能与另一个桌面客户端不同，请先核对账号。

```sh
python skills/task-handoff/scripts/quota_monitor.py --state-dir /absolute/private/monitor once
python skills/task-handoff/scripts/quota_monitor.py --state-dir /absolute/private/monitor checkpoint --task-key example-task --path /absolute/private/handoffs/example.md --arm
python skills/task-handoff/scripts/quota_monitor.py --state-dir /absolute/private/monitor watch --interval 120
```

阶段结束后更新 checkpoint 路径，省略 `--arm`；任务完成或取消时执行 `disarm --task-key example-task`。只有需要重新启用该任务监测时才传 `--arm`。

`status` 返回可供 agent 读取的 JSON：`quota`、`checkpoints`、`events`。达到阈值时，将保存的检查点写入状态目录的 `handoffs/`，同时记录事件。没有关联检查点时，只更新额度，不虚构交接。读取失败、没有可用额度窗口或记录超过 5 分钟时标记 `unknown`。不会把未知额度当成充足额度。

可通过 `--codex /path/to/codex`、`--limit-id codex`、`--warning 20`、`--critical 10` 配置。多额度池按所选 limit ID 读取，不能用另一个模型额度池替代；检查所有有效窗口并取最小剩余比例。

### 可选学习终端桥接

工作台需要提供 `agent-client.py transfer` 登记接口。监测器生成事件之后运行：

```sh
python integrations/learning_terminal.py --state-dir /absolute/private/monitor --client /absolute/learning-terminal/frontend/agent-client.py
```

成功收到登记回执后才标记已投递，失败保留待重试。依赖学习终端按交接内容去重，防止进程在登记后中断造成重复任务。它不会选择接收方或派发。运行宿主可周期调用桥接；工作台的内置集成可直接消费同类事件。

## 数据与恢复边界

状态目录包含私有任务正文，请保留在本机。代码仓库不包含真实任务、账号余额、数据库、认证文件或用户路径。脚本只通过已登录 CLI 查询额度，不读取或导出认证文件，不消费额度重置积分，也不执行任意外部命令。

检查点标注保存时间；其后的工作、仍在运行的进程及结果不明的操作，需要接收方核查。转交前确认原执行方已停止，避免两个 agent 同时修改文件。额度监测依赖宿主持续运行；关闭进程后停止监测。

## 验证

```sh
python -m unittest discover -s tests -v
```

测试使用模拟额度和临时目录，不访问真实账号、不调用模型。官方接口依据：[Codex App Server](https://learn.chatgpt.com/docs/app-server)。
