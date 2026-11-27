"""英文词典（四）：本机 Agent（Codex、Claude Code，runtime/localagents.py、runtime/external.py）"""

EN4: dict[str, str] = {
    "不认识的 Agent：{0}": "Unknown agent: {0}",
    "没找到 {0}。{1}": "{0} not found. {1}",
    "没找到 Node.js，适配器跑不起来。装一个 Node.js 再试。": "Node.js not found, so the adapter can't run. Install Node.js and try again.",
    "{0} 的适配器还没装：在 设置 → 编程 → 本机 Agent 里点「装适配器」。": "The {0} adapter isn't installed: go to Settings → Coding → Local agents and click \"Install adapters\".",
    "没找到 npm（装 Node.js 时会带上）。装好 Node.js 再试。": "npm not found (it comes with Node.js). Install Node.js and try again.",
    "装适配器超时了，看看网络再试。": "Installing the adapters timed out. Check your network and try again.",
    "{0} 只能在项目里做：先选一个项目。": "{0} only works inside a project: pick a project first.",
    "这个任务是 {0} 在做：要它出方案或审查改动，直接用话跟它说。": "{0} is doing this task: to get a plan or a review, just ask it in words.",
    "{0} 改的文件艾拉没存底片，撤销不了：用 git 看改动、还原。": "Aila has no snapshots of files {0} changed, so they can't be undone here: use git to review and revert.",
    "{0} 自己管上下文：要压缩就跟它说 /compact。": "{0} manages its own context: send it /compact to compress.",
    "{0} 的上下文在它自己那边，这里看不到。": "{0} keeps its context on its side; it can't be shown here.",
}
