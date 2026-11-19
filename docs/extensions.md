# 写一个艾拉的扩展包

扩展包能一次给她加上三样东西：新的 `/命令`、做事的「本事」、外部工具。导入一次就都装上，也能一起停用、卸载。

仓库里有两个例子，照着改最快：

- [`examples/extensions/git-helper`](../examples/extensions/git-helper)：只有命令和本事，不运行任何程序
- [`examples/extensions/dice`](../examples/extensions/dice)：带一个工具服务（用 Python 标准库写的 MCP 服务）和一项设置

## 长什么样

```
my-extension/
  aila-extension.json          说明书（必须有）
  commands/<名字>.md           新的 /命令
  skills/<名字>/SKILL.md       一项本事（同一个文件夹里还能放例子、模板）
  server/...                   工具服务的程序（在说明书的 mcpServers 里写怎么启动）
```

### 说明书 `aila-extension.json`

```json
{
  "id": "jira-helper",
  "name": "Jira 助手",
  "version": "1.0.0",
  "aila": ">=0.1",
  "description": "一句话说清楚它是做什么的",
  "requires": ["node"],
  "mcpServers": {
    "jira": {
      "command": "node",
      "args": ["${extension}/server/index.js"],
      "env": { "JIRA_TOKEN": "${setting:token}" },
      "readOnly": false
    }
  },
  "settings": [
    { "key": "token", "label": "Jira 令牌", "secret": true, "required": true, "description": "在 Jira 的个人设置里生成" }
  ]
}
```

| 字段 | 说明 |
|---|---|
| `id` | 小写字母、数字、减号，最多 24 个字；装在 `<数据目录>/extensions/<id>/` |
| `name`、`description` | 显示用 |
| `version` | `1.0.0` 这样；同 id 再导入一次就是升级（开关、设置、「以后都不用问」保留） |
| `aila` | 要求艾拉的最低版本，只认 `>=x.y.z` |
| `requires` | 工具服务要用到的程序（`node`、`py`、`uvx`……）；找不到时装之前会提醒，不会替你装 |
| `mcpServers` | 工具服务：名字（字母数字下划线，最多 7 个字）→ 怎么启动。只支持在本机启动的程序（stdio），不支持远程地址 |
| `settings` | 要你填的设置；`secret` 的只作为环境变量传给它自己的服务，界面上不回显 |

`command`、`args`、`env` 里能用两个占位：`${extension}` 换成扩展装好以后的文件夹，`${setting:键}` 换成你填的设置。
工具服务的工作目录就是扩展文件夹。

### 命令 `commands/<名字>.md`

和项目里 `.aila/commands/` 的写法一样：开头可选一段 `description`，下面是交给她的话，`$ARGUMENTS` 换成命令后面写的内容。

```markdown
---
description: 按最近的提交写一段更新日志
---
帮我写一段更新日志，从 $ARGUMENTS 开始……
```

同名时项目里的命令优先，其次是你自己的（`<数据目录>/commands/`），最后才是扩展的。

### 本事 `skills/<名字>/SKILL.md`

写给她看的做事说明：某类事怎么做、按什么格式、注意什么。开头的 `description` 是一句话简介。

```markdown
---
description: 按约定式提交的格式写提交信息，项目里有 commitlint 或者用户要求时用
---
# 约定式提交
……
```

为什么这样设计：会话开头只放每项本事的名字和简介（不占多少上下文），她判断用得上时才用 `read_skill` 工具读全文，
也能读同一个文件夹里附带的文件（`read_skill` 的 `file` 参数）。本事在开会话时定下来，之后装卸扩展不影响已经开着的会话（前缀缓存不断）。

### 工具服务

就是一个 MCP 服务（Model Context Protocol）：从标准输入每行读一条 JSON-RPC 请求，往标准输出每行写一条回复。
用现成的 MCP SDK 写，或者照 `examples/extensions/dice/server.py` 自己写（要处理的只有 `initialize`、`tools/list`、`tools/call`、`ping`）。

- 工具标了 `annotations.readOnlyHint: true`，或者整个服务在说明书里写 `"readOnly": true`：算只读，她用的时候不问
- 别的工具：只读模式下不能用；另外两种模式每次都问你，可以选「这个工具以后都不用问」
- 工具在编程任务和对话里都能用（对话里每个服务是一个技能包，默认关着）
- 工具叫 `mcp__<扩展id>-<服务名>__<工具名>`

## 怎么装

- 设置 →「扩展」→「选一个 .zip」或「选一个文件夹」，看完清单点「装」
- 终端：`aila ext install <zip 或文件夹>`（也有 `aila ext list / enable / disable / remove <id>`）

装好以后：命令和本事在新开的会话里就能用；工具服务先停着，在「扩展」里打开（有必填的设置要先填）。

## 安全边界

- 扩展的代码只在单独的进程里跑，不会被加载进 AI 服务；它改不了权限规则，读不到艾拉的 `.env`
- 装之前的清单（会加什么命令和本事、会运行什么程序、要填什么）由代码从说明书和文件算出来，不用扩展自己的介绍
- 装好时工具服务是停着的；危险命令、改文件还是照原来的权限规则问你
- 本事是给她的做事说明，装扩展等于你信任它的内容：只装信得过的扩展
- 解压时挡掉 `../`、绝对路径、链接，最多 2000 个文件、200MB

## 打包发给别人

把扩展文件夹压成 zip 就行（外面多包一层文件夹也认）。别人导入时会看到和你一样的清单。
