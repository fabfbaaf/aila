<script setup lang="ts">
defineProps<{ modelValue: boolean; disabled?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: boolean] }>()
</script>

<template>
  <button
    type="button"
    class="toggle"
    :class="{ on: modelValue }"
    role="switch"
    :aria-checked="modelValue"
    :disabled="disabled"
    @click="emit('update:modelValue', !modelValue)"
  >
    <span class="knob"></span>
  </button>
</template>

<style scoped>
.toggle {
  position: relative;
  width: 38px;
  height: 22px;
  padding: 0;
  border: none;
  border-radius: 11px;
  background: #d5d8e3;
  cursor: pointer;
  transition: background 0.15s;
}

.toggle.on {
  background: var(--accent, #5b6ee1);
}

.toggle:disabled {
  opacity: 0.5;
  cursor: default;
}

.knob {
  position: absolute;
  top: 3px;
  left: 3px;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: #fff;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.2);
  transition: left 0.15s;
}

.on .knob {
  left: 19px;
}
</style>
