<script setup lang="ts">
/** 一个线条图标（图形在 icons.ts）；颜色跟着文字，大小默认 18 */
import { ICONS, type IconName } from './icons'

withDefaults(defineProps<{ name: IconName; size?: number; stroke?: number }>(), { size: 18, stroke: 1.7 })
</script>

<template>
  <svg
    class="icon"
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    :stroke-width="stroke"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <path v-for="(d, i) in ICONS[name]" :key="i" :d="d" />
  </svg>
</template>

<style scoped>
.icon {
  flex: none;
  display: block;
}
</style>
