# 艾拉 · 程序员的 AI 桌面伙伴

常驻桌面的 3D 数字人助手。当前进度：**第 1 周完成，第 2 周文字对话已打通**——透明桌面窗口、角色渲染、待机动作、点击互动、拖动；和她打字聊天，她边说边做表情和动作。

## 运行

对话要先启动本地 AI 服务（在旁边的 `aila-ai` 目录，见它的 README）：

```powershell
cd ..\aila-ai
.venv\Scripts\python -m app   # 127.0.0.1:8765；没填密钥时用假模型
```

然后启动桌面端：

```bash
npm install
npm run dev        # 启动桌面版（Electron）
npm run dev:web    # 只在浏览器里调试角色、行为和对话，地址 http://localhost:5180
npm run typecheck  # 类型检查
npm run build      # 打包到 out/
```

## 怎么玩

| 操作 | 艾拉的反应 |
| --- | --- |
| 双击她 / `Alt+Shift+A` / 右键「和她说话」 | 打开对话框；Enter 发送，Esc 收起 |
| 和她聊天 | 回复按说话节奏一个字一个字出现，嘴巴跟着动；表情和动作在对应那句话出现时触发 |
| 她说话时再发一句 | 打断她，直接回答新的问题 |
| 移动鼠标 | 眼睛和头跟着鼠标转（整个屏幕范围） |
| 点她的头 | 缩一下、害羞地低头歪头；连续戳会有不同台词 |
| 点她的身体 | 吓一跳往后仰 |
| 按住拖动 | 身体跟着晃；松手后靠近屏幕边缘会自动贴边 |
| 8 秒不动鼠标 | 开始东张西望 |
| 5 分钟不动鼠标 | 犯困低头；动一下鼠标会惊醒 |
| 右键她 | 菜单：和她说话、加载 VRM 模型、回到右下角、开发者工具、退出 |

她能做的表情：开心、难过、惊讶、放松；动作：点头、摇头、歪头、思考、鞠躬。由大模型在回复里用标签决定（`[happy]`、`[nod]`），解析在 `aila-ai` 里做。

角色以外的透明区域会让鼠标穿透到桌面，不影响正常使用电脑。

## 临时模型

`models/` 里的模型不进 Git（体积大），需要时从原地址下载：

| 文件 | 说明 | 许可 | 下载 |
| --- | --- | --- | --- |
| `Vita.vrm` | 当前默认，银白短发、科技感 | CC0（pixiv 随 VRoid Studio 测试版发布的预设角色） | [vrm-samples](https://github.com/madjin/vrm-samples/tree/master/vroid/beta) |
| `Sendagaya_Shino.vrm` | 黑长直，安静款 | CC0 | 同上 |
| `Victoria_Rubin.vrm` | 浅金长发，甜美款 | CC0 | 同上 |
| `sample.vrm` | three-vrm 官方示例（VRM 1.0） | VRM 1.0 许可，可商用、可再分发 | [three-vrm](https://github.com/pixiv/three-vrm) |

pixiv 对 VRoid 示例模型许可的说明：[Do VRoid Studio's sample models come with conditions of use?](https://vroid.pixiv.help/hc/en-us/articles/4402614652569)

这些只是开发期占位，正式版换成原创形象。

## 换成正式形象

1. 用免费的 [VRoid Studio](https://vroid.com/studio) 捏一个角色，导出为 `.vrm`
2. 右键艾拉 → 加载 VRM 模型
3. 下次启动会自动加载上次的模型

没有模型时显示简易占位角色，所有互动照常可用。

## 目录结构

```
src/
  main/index.ts                 主进程：透明窗口、鼠标穿透、拖动贴边、全局光标、右键菜单、快捷键、对话转发
  preload/index.ts              渲染层能调用的桌面能力（contextBridge）
  shared/chat.ts                主进程和渲染层共用：对话事件类型、SSE 解析、请求 AI 服务
  renderer/src/
    App.vue                     把鼠标事件接到行为系统，显示对话气泡和输入框
    chat/Conversation.ts        对话：历史、流式回复按说话节奏放出、标签同步触发表情动作、打断
    chat/transport.ts           桌面版经主进程转发；浏览器调试走 Vite 代理
    components/ChatInput.vue    输入框（中文输入法选字时的回车不会误发送）
    scene/Stage.ts              Three.js 舞台：渲染、构图、像素级命中检测
    behavior/Behavior.ts        行为系统：待机、眨眼、视线、反应、犯困、拖动、对话表情、动作、口型
    avatar/types.ts             角色统一接口
    avatar/PlaceholderAvatar.ts 占位角色（基本几何体）
    avatar/VrmAvatar.ts         VRM 角色（@pixiv/three-vrm）
    persona/lines.ts            艾拉的台词
```

## 几个设计点

- **鼠标穿透**：窗口默认穿透；渲染层读取鼠标下那个像素的透明度，落在角色身上才接收点击。比射线检测更便宜，也不受模型面数影响。
- **视线跟随整个屏幕**：窗口外的鼠标事件收不到，所以主进程每 33ms 轮询一次全局光标位置，推给渲染层。
- **行为和渲染分离**：`Behavior` 只算表情权重和姿态参数，角色实现只负责画出来。占位角色和 VRM 角色共用同一套行为。
- **平滑过渡**：所有表情和姿态都用和帧率无关的指数平滑，动作之间不会跳变。
- **动作和那句话同步**：网络一次可能来好几个字，也可能卡一下。回复先进队列，再按固定节奏（每秒 18 字，句号后停顿）放出来；标签排在文字中间，放到那里才触发。所以「[nod]今天也请多指教」一定是说到这句时才点头。
- **对话请求走主进程**：渲染层不用放开 CSP 和跨域，打包成 `file://` 后也一样能用；浏览器调试时走 Vite 代理，两条路共用同一套 SSE 解析。
- **一定有结局**：AI 服务没开、密钥错、余额不足、中途断开，都会归类成不同的错误，艾拉用不同的话回应，不会卡在「……」。
- **打断只记已说出口的部分**：被新消息打断时，历史里只存已经显示出来的那段，大模型不会以为用户听完了整句话（第 3 周语音打断也靠这个）。

## 已知待验证

- 放下手臂的角度（`VrmAvatar.ts` 里的 `1.2`）已在 VRM 1.0（`sample.vrm`）和 VRM 0.x（`Vita.vrm`）上验证，手臂自然下垂；体型差别大的模型可能还要微调。
- 表情名按大小写不敏感匹配（VRoid 0.x 模型的惊讶表情叫 `Surprised`）。
