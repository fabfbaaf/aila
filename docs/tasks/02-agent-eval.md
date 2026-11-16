# 02 编程 Agent 评测：任务集、跑任务、自动判定、出报告

## 背景

艾拉的编程 Agent（`aila-ai/app/runtime/`）要拿真实任务评测，记下这些数（简历和技术博客要用）：

- 任务成功率
- 平均步数（一步 = 一次模型请求 + 它要的工具）、平均用时
- 每个任务的 token：输入、命中缓存的输入、输出；缓存命中率
- 失败的原因分布（测试没过、步数用完、出错、被拒绝……）

现在还没接真的大模型，所以这次只搭好脚手架：用假模型和剧本模型把整条链路跑通；接上密钥以后改个参数就能真跑。

## 要做的

### 任务格式：`aila-ai/eval/tasks/<任务 id>/task.json`

```json
{
  "id": "fix-cart-total",
  "title": "修好购物车总价少算运费的 bug",
  "fixture": "repo",
  "prompt": "购物车的总价少算了运费，测试 test_total_with_shipping 没过。修好它，跑测试确认通过。",
  "check": { "command": "python -m pytest -q", "expect_exit": 0 },
  "answer_contains": [],
  "max_steps": 30,
  "timeout_s": 600,
  "tags": ["bugfix", "python"]
}
```

- `fixture`：任务目录下的一个子目录，是要被改的小项目；跑的时候复制到临时目录、`git init` 并提交一次（她可以用 `git diff` 看自己改了什么）
- `check.command`：她做完以后在那个临时项目里跑，退出码等于 `expect_exit` 算做成；命令里的 `python` 换成跑评测的这个 python（`sys.executable`）
- `answer_contains`：只读类任务（「下单在哪处理」）用：她最后的回答里要包含这些词（都包含才算）。`check` 和 `answer_contains` 至少有一个

先写 5 个任务（都用小的 Python 项目，每个项目自带测试）：

1. 修一个让测试失败的 bug（单文件）
2. 加一个函数并写测试，跑通
3. 改一个函数名，所有调用的地方一起改（跨 3 个文件）
4. 按报错定位：给一段带调用栈的报错，修好
5. 只读：问「X 是在哪个函数里处理的」，检查回答里有那个函数名

### 跑任务：`aila-ai/scripts/eval_agent.py`

```
.venv\Scripts\python -X utf8 -m scripts.eval_agent                 # 全部任务，用设置里的模型
.venv\Scripts\python -X utf8 -m scripts.eval_agent --only fix-cart-total --repeat 3
.venv\Scripts\python -X utf8 -m scripts.eval_agent --model mock     # 假模型：只验证链路
```

- 每个任务：复制项目 → 新建一个 `Runtime`（数据目录用临时目录，不碰 `aila-ai/data`）→ 开会话，权限模式 `auto` → 说 `prompt` → 收完这一轮的事件 → 跑 `check` → 记结果
- 要用户确认的操作（`auto` 模式下只剩危险命令和读密钥文件）一律回答「不要」，并在结果里记一笔
- 用 `Runtime` 的公开方法（`create`、`run`、`confirms.answer`）；尽量不改运行时本身，要改的话改动最小并在分支说明里写清楚
- 模型：默认用 `app.deps.get_coder_model()`（设置里的模型）；`--model mock` 用 `app.runtime.mock.MockCoderModel`
- 输出到 `aila-ai/eval/results/<时间>/`（这个目录加进 `.gitignore`）：
  - `results.jsonl`：每个任务每次一行：id、做成没有、失败原因、步数、各工具调用次数、用时、token（输入 / 命中缓存 / 没命中 / 输出）、缓存命中率、轮次结束原因、`check` 的输出最后 30 行
  - `summary.md`：一张总表（每个任务一行）加合计：成功率、平均步数、平均用时、总 token、总体缓存命中率；有 `AILA_PRICE_HIT`、`AILA_PRICE_MISS`、`AILA_PRICE_OUTPUT`（每百万 token 的价格，元）环境变量时算出费用，没有就不算
- 进度打到终端：每个任务一行（✓ / ✗、步数、用时、命中率）

## 不要改

- `aila-ai/app/runtime/` 里的文件（除非实在需要，见上面）
- 桌面端

## 验收

- `aila-ai/tests/test_eval_agent.py`：
  - 用剧本模型（参考 `tests/test_runtime.py` 的 `Scripted`）完成一个任务：判定为做成，指标都对（步数、token、命中率）
  - 剧本模型改错：`check` 没过，判定为没做成，原因是「检查没通过」
  - 步数用完：原因是 `max_steps`
  - 只读任务：回答里有 / 没有要求的词
  - `summary.md` 的合计算得对
- `--model mock` 能把 5 个任务都跑完（假模型做不成是正常的，链路通就行）
- `aila-ai/README.md` 加一节「编程 Agent 评测」：怎么写任务、怎么跑、结果在哪
- `AGENTS.md` 里的测试全过
