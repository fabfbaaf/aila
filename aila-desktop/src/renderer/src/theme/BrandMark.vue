<script setup lang="ts">
/**
 * 艾拉的标志：「艾拉」两个字（志莽行书）写在深色圆角方块上，和 exe、托盘的图标同一个样子（scripts/make-icon.mjs）。
 * size：方块边长（像素）；tile=false 时只有字（启动卡片上大字用）。小于 36 时只写「艾」，两个字太小认不出来。
 * 颜色和字体在 tokens.css（--ui-brand-*），这里不写死。
 */
import { computed } from 'vue'

const props = withDefaults(defineProps<{ size?: number; tile?: boolean }>(), { size: 40, tile: true })

const text = computed(() => (props.size < 36 ? '艾' : '艾拉'))
const fontSize = computed(() => props.size * (props.size < 36 ? 0.78 : 0.44))
</script>

<template>
  <span
    class="brand-mark"
    :class="{ tile }"
    role="img"
    aria-label="艾拉"
    :style="{ width: tile ? `${size}px` : undefined, height: tile ? `${size}px` : undefined, borderRadius: `${Math.round(size * 0.23)}px` }"
  >
    <span class="t" :style="{ fontSize: `${tile ? fontSize : size}px` }">{{ tile ? text : '艾拉' }}</span>
  </span>
</template>

<style scoped>
.brand-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: none;
}

.brand-mark.tile {
  background: var(--ui-brand-tile);
  box-shadow: 0 4px 14px rgba(167, 139, 250, 0.22), inset 0 0 0 1px rgba(196, 181, 253, 0.12);
}

.t {
  font-family: var(--ui-brand-font);
  line-height: 1;
  white-space: nowrap;
  padding-bottom: 0.06em;
  background: var(--ui-brand-text);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
}
</style>
