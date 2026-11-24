<script setup lang="ts">
/** 把原来设置窗口里的一个分区（资料库、她记得的事）当成主窗口里的一页 */
import { t } from '../i18n'
import type { Component } from 'vue'
import { bridge } from '../platform/bridge'

defineProps<{ section: Component }>()
</script>

<template>
  <div class="settings-scope section-page">
    <div v-if="!bridge" class="empty">{{ t('这一页只在桌面版里能用（npm run dev）。') }}</div>
    <div v-else class="inner">
      <component :is="section" />
    </div>
  </div>
</template>

<style scoped>
.section-page {
  height: 100%;
  overflow-y: auto;
  background: var(--bg);
}

.inner {
  max-width: 860px;
  padding: 24px 32px 40px;
}
</style>
