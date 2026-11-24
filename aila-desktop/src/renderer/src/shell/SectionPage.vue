<script setup lang="ts">
/** 把原来设置窗口里的一个分区（资料库、她记得的事）当成主窗口里的一页；顶上一条和别的页一样的标题栏 */
import { t } from '../i18n'
import type { Component } from 'vue'
import { bridge } from '../platform/bridge'

/** title、hint 由主窗口按现在的语言翻好再传进来 */
defineProps<{ section: Component; title: string; hint: string }>()
</script>

<template>
  <div class="section-wrap">
    <header class="bar">
      <b>{{ title }}</b>
      <span class="hint">{{ hint }}</span>
    </header>
    <div class="settings-scope section-page">
      <div v-if="!bridge" class="empty">{{ t('这一页只在桌面版里能用（npm run dev）。') }}</div>
      <div v-else class="inner">
        <component :is="section" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.section-wrap {
  display: flex;
  flex-direction: column;
  height: 100%;
}

/* 和项目、对话、作品页一样的标题栏 */
.bar {
  display: flex;
  align-items: center;
  gap: 14px;
  min-height: 52px;
  box-sizing: border-box;
  padding: 0 18px;
  border-bottom: 1px solid var(--ui-line);
  background: var(--ui-bg);
  flex: none;
}

.bar b {
  font-size: 15px;
}

.hint {
  color: var(--ui-muted);
  font-size: 12.5px;
}

.section-page {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  background: var(--bg);
}

.inner {
  max-width: 860px;
  padding: 24px 32px 40px;
}
</style>
