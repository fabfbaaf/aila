<script setup lang="ts">
/** 过程和轨迹共用这条撤销记录，重放时也显示跳过的原因。 */
import { t, joinList } from '../i18n'
import type { AgentUndoEvent } from '../../../shared/agent'

defineProps<{ result: AgentUndoEvent }>()
</script>

<template>
  <div class="undo-result" role="status">
    <strong>{{ t('撤销结果') }}</strong>
    <div v-if="result.restored.length">{{ t('已撤销 {n} 个文件的改动：', { n: result.restored.length }) }}{{ joinList(result.restored) }}</div>
    <div v-else>{{ t('没有撤销任何文件的改动。') }}</div>
    <ul v-if="Object.keys(result.skipped).length">
      <li v-for="(reason, file) in result.skipped" :key="file">{{ t('跳过') }} <code>{{ file }}</code>：{{ reason }}</li>
    </ul>
    <div v-if="result.commands">{{ t('这一轮还跑过命令，命令做的事没有撤销。') }}</div>
  </div>
</template>

<style scoped>
.undo-result {
  margin: 8px 0;
  padding: 8px 12px;
  border-left: 3px solid var(--accent);
  border-radius: 4px;
  background: var(--accent-bg);
  color: var(--text);
  font-size: 13px;
  overflow-wrap: anywhere;
}

ul {
  margin: 4px 0;
  padding-left: 18px;
}
</style>
