<script setup lang="ts">
/**
 * 一段讲解：AI 说的（一句话、展开、要注意的）和代码算出来的事实分开放；
 * 没讲成（没填密钥、额度、网络）时只给事实，说一下为什么。「念给我听」让桌面上的她念出来。
 */
import { ref } from 'vue'
import type { Explanation } from '../../../shared/understand'
import { t } from '../i18n'

const props = defineProps<{ value: Explanation | null; busy: boolean }>()
const emit = defineEmits<{ say: [text: string] }>()
const showFacts = ref(false)

function readAloud(): void {
  const e = props.value?.explanation
  if (e) emit('say', [e.summary, e.detail, e.watch].filter(Boolean).join(' '))
}
</script>

<template>
  <div class="explain" :aria-busy="busy">
    <p v-if="busy" class="dim">{{ t('她在看代码，想一想怎么讲……') }}</p>
    <template v-else-if="value">
      <template v-if="value.explanation">
        <p class="summary">{{ value.explanation.summary }}</p>
        <p v-if="value.explanation.detail" class="detail">{{ value.explanation.detail }}</p>
        <p v-if="value.explanation.watch" class="watch"><b>{{ t('要注意') }}</b> {{ value.explanation.watch }}</p>
        <div class="row">
          <button type="button" class="ghost" @click="readAloud">{{ t('念给我听') }}</button>
          <span class="dim">{{ value.cached ? t('讲过的，没再花钱') : t('AI 讲的，可能有错；结构和事实是代码算的') }}</span>
        </div>
      </template>
      <p v-else-if="value.note" class="note">{{ value.note }}</p>
      <button v-if="value.facts" type="button" class="link" :aria-expanded="showFacts" @click="showFacts = !showFacts">
        {{ showFacts ? t('收起事实') : t('看代码算出来的事实') }}
      </button>
      <pre v-if="showFacts && value.facts" class="facts">{{ value.facts }}</pre>
    </template>
  </div>
</template>

<style scoped>
.explain {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 10px 12px;
  border: 1px solid var(--ui-accent-line);
  border-radius: var(--ui-r-s);
  background: var(--ui-accent-soft);
}

.summary {
  margin: 0;
  color: var(--ui-text);
  font-weight: 600;
}

.detail,
.watch,
.note {
  margin: 0;
  color: var(--ui-text-2);
  font-size: 13px;
  line-height: 1.6;
}

.watch b {
  color: var(--ui-warn, #d97706);
  font-weight: 600;
}

.row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.dim {
  margin: 0;
  color: var(--ui-muted);
  font-size: 12px;
}

.ghost,
.link {
  padding: 2px 10px;
  border: 1px solid var(--ui-line);
  border-radius: 999px;
  background: none;
  color: var(--ui-text-2);
  font-size: 12px;
  cursor: pointer;
}

.link {
  align-self: flex-start;
  padding: 0;
  border: 0;
  color: var(--ui-accent);
  text-decoration: underline;
}

.facts {
  max-height: 240px;
  margin: 0;
  overflow: auto;
  padding: 8px;
  border-radius: var(--ui-r-s);
  background: var(--ui-sunken);
  color: var(--ui-text-2);
  font: 11.5px/1.5 var(--ui-mono);
  white-space: pre-wrap;
}
</style>
