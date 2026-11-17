<script setup lang="ts">
import { computed } from 'vue'
import { bridge } from '../../platform/bridge'
import { store } from '../store'

const api = bridge!
const fileName = computed(() => store.modelPath?.split(/[\\/]/).pop() ?? '')
</script>

<template>
  <h2>形象</h2>
  <p class="lead">她在桌面上的样子。默认是她自己的原创形象；也可以换成别的 VRM 格式的 3D 模型（VRoid Studio 可以免费捏一个导出）。</p>

  <div class="card">
    <div class="row">
      <span class="label">现在的模型</span>
      <div>
        <span v-if="store.modelPath">{{ fileName }}</span>
        <span v-else>艾拉自己的形象（原创，随程序自带）</span>
        <span v-if="store.modelPath" class="hint">{{ store.modelPath }}</span>
      </div>
    </div>
    <div class="actions">
      <button class="btn primary" @click="api.modelChoose()">选择 VRM 模型…</button>
      <button v-if="store.modelPath" class="btn" @click="api.modelBuiltin()">换回艾拉自己的形象</button>
      <span class="hint">下次启动会自动加载上次选的模型。</span>
    </div>
  </div>

  <div class="card">
    <h3>位置</h3>
    <p class="hint" style="margin-top: 0">按住她可以拖到屏幕上任何地方，靠近边缘会自动贴边。找不到她的时候：</p>
    <div class="actions">
      <button class="btn" @click="api.resetPosition()">让她回到右下角</button>
    </div>
  </div>
</template>
