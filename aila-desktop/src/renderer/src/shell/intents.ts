/**
 * 快捷跳转（Ctrl+K）要别的页面接着做的事：开新任务、在对话页里搜某段话。
 * 页面都在 KeepAlive 里留着，各自看着这几个值，变了就照做（比通过地址传更简单，也不会把搜的词写进地址）。
 */
import { ref } from 'vue'

/** 每次加一：任务页打开「开一个新任务」 */
export const newTaskRequest = ref(0)
/** 对话页的搜框里填上这段（填完清空） */
export const chatQueryRequest = ref('')
/** 开场演出里她借你的鼠标在对话框里打字：输入框里的字跟着变（null = 不管；只填不发） */
export const chatDraftRequest = ref<string | null>(null)
