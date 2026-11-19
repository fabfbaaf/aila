# 写一个艾拉的扩展包

扩展包能一次给她加上新的 `/命令`、做事的「本事」、外部工具，还有桌面上的她的动作、台词、形象，和自带的小页面。导入一次就都装上，也能一起停用、卸载；也能从「目录」里浏览、下载。

仓库里有三个例子，照着改最快：

- [`examples/extensions/git-helper`](../examples/extensions/git-helper)：只有命令和本事，不运行任何程序
- [`examples/extensions/dice`](../examples/extensions/dice)：带一个工具服务（用 Python 标准库写的 MCP 服务）和一项设置
- [`examples/extensions/cheer`](../examples/extensions/cheer)：两个动作、几句台词、一个番茄钟页面

## 长什么样

```
my-extension/
  aila-extension.json          说明书（必须有）
  commands/<名字>.md           新的 /命令
  skills/<名字>/SKILL.md       一项本事（同一个文件夹里还能放例子、模板）
  server/...                   工具服务的程序（在说明书的 mcpServers 里写怎么启动）
  motions/<名字>.json          桌面上的她的动作
  lines.json                   台词
  avatars/<名字>.vrm           形象（VRM；Live2D 是 avatars/<名字>/ 整个文件夹）
  pages/<名字>.html            自带的小页面（沙盒窗口里打开）
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

## 桌面伙伴：动作、台词、形象

例子：[`examples/extensions/cheer`](../examples/extensions/cheer)。

### 动作 `motions/<名字>.json`

和内置的点头、挥手同一个格式：几段姿态，每段多久、头和手到什么程度、带什么表情。

```json
{
  "label": "欢呼（测试过了的时候）",
  "phases": [
    { "dur": 0.25, "expr": { "happy": 0.6 }, "pose": { "armsUp": 0.8, "headPitch": -0.1 } },
    { "dur": 0.5, "expr": { "happy": 0.4 }, "pose": { "armsUp": 0 } }
  ]
}
```

| 键 | 范围 | 意思 |
|---|---|---|
| `dur` | 0.05–5 秒 | 这一段多久（整个动作最多 10 秒、40 段） |
| `pose.headPitch` | -0.6–0.6 | 正数低头 |
| `pose.headYaw` | -0.8–0.8 | 正数转向她的左边 |
| `pose.headRoll` | -0.5–0.5 | 正数歪头 |
| `pose.bodyLean` / `bodySway` | -0.3–0.3 | 身体后仰 / 左右晃 |
| `pose.armRaise` | 0–1 | 抬右手（挥手的那个姿势） |
| `pose.forearmSwing` | -1–1 | 抬手时小臂左右摆 |
| `pose.armsUp` | 0–1 | 两只手举过头 |
| `expr.happy` / `sad` / `surprised` / `relaxed` / `blink` | 0–1 | 表情 |

为什么只能动这些值、不能直接转骨骼：范围由代码限住，换哪个模型都不会把她扭成奇怪的样子。超出范围的值截到边上，不认识的键导入时就报错。

动作名（文件名）是小写字母开头的 2–20 个字母、数字、下划线，不能和内置的动作、表情重名。
装好以后：扩展页里点「试一下」她就做一遍；对话里她也会用——系统提示词里列出扩展的动作，她回答时写 `[cheer]` 这样的标签就会做。

### 台词 `lines.json`

```json
{ "replace": false, "lines": { "pokeHead": ["……又、又戳？"], "wake": ["……嗯？我只是闭目养神。"] } }
```

往她各种场合的台词池里加句子；`"replace": true` 是整组换掉（装之前清单里会提醒）。能用的场合：
`chatOpen` 打开对话、`pokeHead` 被戳头、`pokeHeadMany` 一直被戳、`pokeBody` 被戳身上、`dragStart` 被拖起来、`dragEnd` 被放下、
`wake` 睡醒、`sleepy` 犯困、`wakeHere` 被叫醒、`forgetOne` 忘掉一件事、`agentStart` 开始做任务、`agentTaskStarted` 任务开始了、
`testsRecovered` 测试修好了、`testsStruggling` 测试一直挂、`workMaking` 在写作品、`lateNight` 深夜。每句最多 80 个字，每个场合最多 30 句。

### 形象 `avatars/<名字>.vrm` 或 `avatars/<名字>/`

放 VRM 模型（最大 100MB），或者一个 Live2D 模型的整个文件夹：`avatars/<名字>/` 里放且只放一个 `.model3.json`，贴图、动作、物理文件按它里面写的相对路径放在同一个文件夹里（最多 500 个文件、200MB）。Live2D 形象要用的人先装好 Live2D 运行库（`npm run setup:live2d`）。

装好以后在扩展页点「换上」；想换回来去「形象」点「换回艾拉自己的形象」。
**只放你有权分发的模型**（自己做的，或者许可证允许再分发的）。

## 扩展自带的页面 `pages/<名字>.html`

一个自包含的网页（脚本、样式写在页面里，图片用 data: 地址），`<title>` 是显示的名字。
在扩展页点「打开」，开在单独的沙盒窗口里：和她的作品一样不能联网、不能加载外面的东西、碰不到艾拉的服务，链接和新窗口都不跟。
适合放小工具（番茄钟、速查表、计算器）。例子：`examples/extensions/cheer/pages/pomodoro.html`。

## 目录

目录是一份 `catalog.json`，列着能装的扩展：

```json
{
  "name": "我们组的扩展",
  "extensions": [
    { "id": "dice", "name": "骰子", "version": "1.0.0", "description": "……",
      "url": "dice-1.0.0.zip", "sha256": "64 位十六进制", "size": 12345 }
  ]
}
```

- 在扩展页的「目录」里填目录地址（https 网址，或者这台电脑上的完整路径，比如团队共享盘），一行一个
- 点「看看」：下载（只许 https 公网地址，每一跳重定向都检查，最大 50MB）、核对 sha256、包里的 id 要和目录里写的一样，然后给你看和导入时一样的清单
- `url` 相对目录地址算；本机目录里的 zip 只能在目录文件所在的文件夹里
- `examples/extensions/make_catalog.py` 把仓库里的例子打成 zip、生成一份本机目录，试的时候用；放到网上就把 `dist` 里的文件传到 https 地址
- 现在没有官方目录，默认一个目录都没有

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
