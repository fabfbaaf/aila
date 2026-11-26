// 手机上的艾拉：配对、聊天、看任务、开任务（门在 app/phone/gateway.py）。
// 所有她说的话、任务内容都用 textContent 放进页面，不当 HTML（里面可能有网页、代码的原文）。
'use strict'

const $ = (id) => document.getElementById(id)
const KEY = 'aila.phone.token'
const HISTORY = 'aila.phone.chat'
const KEEP = 60

const store = {
  get(k) { try { return localStorage.getItem(k) } catch { return null } },
  set(k, v) { try { localStorage.setItem(k, v) } catch { /* 隐私模式：只是不记住 */ } },
  del(k) { try { localStorage.removeItem(k) } catch { /* 同上 */ } }
}

let token = store.get(KEY) || ''
let history = []
try { history = JSON.parse(store.get(HISTORY) || '[]').slice(-KEEP) } catch { history = [] }

function el(tag, cls, text) {
  const e = document.createElement(tag)
  if (cls) e.className = cls
  if (text !== undefined) e.textContent = text
  return e
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    ...opts,
    headers: { 'content-type': 'application/json', authorization: `Bearer ${token}`, ...(opts.headers || {}) }
  })
  if (res.status === 401) {
    token = ''
    store.del(KEY)
    showPair()
    throw new Error('这台手机要重新配对。')
  }
  if (!res.ok) {
    const data = await res.json().catch(() => ({}))
    throw new Error(typeof data.detail === 'string' ? data.detail : `出错了（${res.status}）`)
  }
  return res
}

/** 读 SSE：一段一段地收，凑满一个事件（空行结尾）才交出去 */
async function readEvents(res, onEvent) {
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true }).replace(/\r/g, '')
    let i
    while ((i = buf.indexOf('\n\n')) >= 0) {
      const chunk = buf.slice(0, i)
      buf = buf.slice(i + 2)
      const data = chunk.split('\n').filter((l) => l.startsWith('data:')).map((l) => l.slice(5).trimStart()).join('\n')
      if (!data) continue
      try { onEvent(JSON.parse(data)) } catch { /* 不认识的行：跳过 */ }
    }
  }
}

// ---------- 配对 ----------

function showPair() {
  $('pair').classList.remove('hidden')
  $('app').classList.add('hidden')
}

function showApp(name) {
  $('pair').classList.add('hidden')
  $('app').classList.remove('hidden')
  $('who').textContent = name ? `${name} · 同一个 Wi-Fi` : ''
}

$('pairGo').onclick = async () => {
  $('pairErr').textContent = ''
  const code = $('pairCode').value.trim()
  if (!/^\d{6}$/.test(code)) { $('pairErr').textContent = '配对码是 6 位数字。'; return }
  try {
    const res = await fetch('/pair', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ code, name: $('pairName').value.trim() || '手机' })
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.detail || '配对没成功。')
    token = data.token
    store.set(KEY, token)
    start()
  } catch (err) {
    $('pairErr').textContent = err.message
  }
}

// ---------- 页签 ----------

function tab(name) {
  $('tabChat').classList.toggle('on', name === 'chat')
  $('tabTasks').classList.toggle('on', name !== 'chat')
  $('chat').classList.toggle('hidden', name !== 'chat')
  $('chatFooter').classList.toggle('hidden', name !== 'chat')
  $('tasks').classList.toggle('hidden', name !== 'tasks')
  $('detail').classList.toggle('hidden', name !== 'detail')
  $('detailFooter').classList.toggle('hidden', name !== 'detail')
  if (name !== 'detail') closeDetail()
  if (name === 'tasks') loadTasks()
}
$('tabChat').onclick = () => tab('chat')
$('tabTasks').onclick = () => tab('tasks')

// ---------- 对话 ----------

function renderHistory() {
  const box = $('chat')
  box.replaceChildren()
  if (!history.length) box.append(el('div', 'status', '她在电脑那边。说句话试试。'))
  for (const m of history) box.append(el('div', `msg ${m.role}`, m.content))
  box.scrollTop = box.scrollHeight
}

function saveHistory() {
  history = history.slice(-KEEP)
  store.set(HISTORY, JSON.stringify(history))
}

let chatting = false
$('chatSend').onclick = async () => {
  const text = $('chatInput').value.trim()
  if (!text || chatting) return
  chatting = true
  $('chatSend').disabled = true
  $('chatInput').value = ''
  history.push({ role: 'user', content: text })
  saveHistory()
  renderHistory()
  const box = $('chat')
  const bubble = el('div', 'msg assistant', '')
  const status = el('div', 'status', '她在想……')
  box.append(status, bubble)
  let reply = ''
  try {
    const res = await api('/api/chat', { method: 'POST', body: JSON.stringify({ messages: history }) })
    await readEvents(res, (ev) => {
      if (ev.type === 'text') { reply += ev.text; bubble.textContent = reply; status.textContent = '' }
      else if (ev.type === 'status') status.textContent = ev.text
      else if (ev.type === 'confirm') box.append(chatConfirm(ev))
      else if (ev.type === 'agent_task') box.append(el('div', 'status', '她开了一个编程任务，在「任务」里能看。'))
      else if (ev.type === 'error') box.append(el('div', 'err', ev.message || '出错了。'))
      box.scrollTop = box.scrollHeight
    })
  } catch (err) {
    box.append(el('div', 'err', err.message))
  }
  status.remove()
  if (reply.trim()) history.push({ role: 'assistant', content: reply })
  else bubble.remove()
  saveHistory()
  chatting = false
  $('chatSend').disabled = false
}

/** 她要确认的操作（对话里的，比如在电脑的浏览器里打开网页）：要做的事由她那边的代码列出来 */
function chatConfirm(ev) {
  const card = el('div', 'ask')
  card.append(el('div', 'q', `要${ev.action || '做这件事'}，可以吗？`))
  if (ev.detail) card.append(el('pre', '', ev.detail))
  const row = el('div', 'row')
  const answer = async (approved) => {
    row.replaceChildren(el('span', 'note', approved ? '你同意了' : '你没同意'))
    try { await api('/api/chat/confirm', { method: 'POST', body: JSON.stringify({ id: ev.id, approved }) }) } catch (err) {
      row.replaceChildren(el('span', 'err', err.message))
    }
  }
  const yes = el('button', 'primary', '可以')
  const no = el('button', '', '不要')
  yes.onclick = () => answer(true)
  no.onclick = () => answer(false)
  row.append(yes, no)
  card.append(row)
  return card
}

// ---------- 任务 ----------

function taskDot(s) {
  if (s.running) return 'run'
  if (s.waiting) return 'wait'
  if (s.outcome === 'done') return 'ok'
  return s.outcome && s.outcome !== 'done' ? 'bad' : ''
}

function taskLine(s) {
  const project = (s.cwd || '').split(/[\\/]/).filter(Boolean).pop() || ''
  if (s.pending) return `${project} · 等你在电脑上同意：${s.pending.action || ''}`
  if (s.running) return `${project} · 在做${s.activity ? `：${s.activity.tool} ${s.activity.target}` : ''}`
  return `${project} · ${s.outcome === 'done' ? '做完了' : s.outcome ? '停下了' : '还没开始'}`
}

async function loadTasks() {
  const list = $('taskList')
  try {
    const items = await (await api('/api/tasks')).json()
    list.replaceChildren()
    if (!items.length) list.append(el('div', 'status', '还没有任务。'))
    for (const s of items.filter((x) => x.kind !== 'computer')) {
      const b = el('button', 'task')
      const t = el('div', 't')
      t.append(el('span', `dot ${taskDot(s)}`), document.createTextNode(s.title || '（还没有标题）'))
      b.append(t, el('div', 's', taskLine(s)))
      b.onclick = () => openDetail(s.id)
      list.append(b)
    }
  } catch (err) {
    list.replaceChildren(el('div', 'err', err.message))
  }
}
$('refresh').onclick = loadTasks

$('newTaskBtn').onclick = async () => {
  const form = $('newTask')
  form.classList.toggle('hidden')
  if (form.classList.contains('hidden')) return
  const sel = $('ntProject')
  sel.replaceChildren()
  try {
    const projects = await (await api('/api/projects')).json()
    if (!projects.length) sel.append(new Option('还没有登记项目：先在电脑上登记一个', ''))
    for (const p of projects) sel.append(new Option(p.name, p.path))
  } catch (err) {
    $('ntErr').textContent = err.message
  }
}

$('ntGo').onclick = async () => {
  $('ntErr').textContent = ''
  const project = $('ntProject').value
  const text = $('ntText').value.trim()
  if (!project || !text) { $('ntErr').textContent = '选好项目，写上要做什么。'; return }
  try {
    const s = await (await api('/api/tasks', { method: 'POST', body: JSON.stringify({ project, text }) })).json()
    $('ntText').value = ''
    $('newTask').classList.add('hidden')
    openDetail(s.id)
  } catch (err) {
    $('ntErr').textContent = err.message
  }
}

// ---------- 一个任务 ----------

let current = ''
let follow = null
let lastSeq = -1
const toolLines = new Map()

const END = { done: '这一轮做完了', error: '这一轮出错了', max_steps: '步数用完了', interrupted: '停下了', rejected: '你没同意，停下了',
  timeout: '一直没等到同意，停下了', denied: '被权限挡住了', budget: '花费到上限了' }

function closeDetail() {
  if (follow) follow.abort()
  follow = null
  current = ''
}

function show(ev) {
  const box = $('dEvents')
  if (typeof ev.seq === 'number') {
    if (ev.seq <= lastSeq) return
    lastSeq = ev.seq
  }
  switch (ev.type) {
    case 'user':
      box.append(el('div', 'msg user', ev.text || ''))
      break
    case 'assistant':
      if ((ev.text || '').trim()) box.append(el('div', 'msg assistant', ev.text))
      break
    case 'tool_start': {
      const line = el('div', 'line', `● ${ev.status || ev.name}`)
      toolLines.set(ev.id, line)
      box.append(line)
      break
    }
    case 'tool_progress': {
      const line = toolLines.get(ev.id)
      if (line) line.textContent = `● ${ev.who ? `${ev.who}：` : ''}${ev.text}`
      break
    }
    case 'tool': {
      const ok = ev.status === 'ok'
      const line = toolLines.get(ev.id) || box.appendChild(el('div', 'line'))
      const first = (ev.content || '').split('\n').find((l) => l.trim()) || ''
      line.replaceChildren(el('span', ok ? 'ok' : 'bad', ok ? '✓ ' : '✗ '), el('b', '', ev.name), document.createTextNode(` ${first.slice(0, 80)}`))
      break
    }
    case 'confirm':
      box.append(el('div', 'line wait', `等你在电脑上同意：${ev.action || ''}`))
      break
    case 'turn_end':
      box.append(el('div', 'status', END[ev.reason] || '这一轮结束了'))
      break
    default:
      return
  }
  box.parentElement.scrollTop = box.parentElement.scrollHeight
}

async function openDetail(id) {
  closeDetail()
  current = id
  lastSeq = -1
  toolLines.clear()
  $('dEvents').replaceChildren()
  tab('detail')
  current = id
  try {
    const s = await (await api(`/api/tasks/${encodeURIComponent(id)}`)).json()
    $('dTitle').textContent = s.title || '任务'
    for (const ev of s.events) show(ev)
    if (s.running) subscribe(id)
  } catch (err) {
    $('dEvents').append(el('div', 'err', err.message))
  }
}

async function subscribe(id) {
  if (follow) follow.abort()
  follow = new AbortController()
  try {
    const res = await api(`/api/tasks/${encodeURIComponent(id)}/events?after=${lastSeq}`, { signal: follow.signal })
    await readEvents(res, (ev) => { if (current === id) show(ev) })
  } catch { /* 切走了、断了：下次打开再接上 */ }
}

$('back').onclick = () => tab('tasks')
$('stop').onclick = async () => {
  if (!current) return
  try { await api(`/api/tasks/${encodeURIComponent(current)}/interrupt`, { method: 'POST' }) } catch (err) {
    $('dEvents').append(el('div', 'err', err.message))
  }
}
$('dSend').onclick = async () => {
  const text = $('dInput').value.trim()
  if (!text || !current) return
  try {
    await api(`/api/tasks/${encodeURIComponent(current)}/messages`, { method: 'POST', body: JSON.stringify({ text }) })
    $('dInput').value = ''
    subscribe(current)
  } catch (err) {
    $('dEvents').append(el('div', 'err', err.message))
  }
}

// ---------- 开始 ----------

async function start() {
  if (!token) return showPair()
  try {
    const me = await (await api('/api/me')).json()
    showApp(me.name)
    renderHistory()
  } catch (err) {
    if (token) { showApp(''); $('chat').replaceChildren(el('div', 'err', `连不上电脑：${err.message}`)) }
  }
}
start()
