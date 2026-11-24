<script setup lang="ts">
/**
 * 启动卡片：打开艾拉时屏幕中间出来的那张「艾拉」卡片，她从卡片里冒出来、跳到右下角（shared/entrance.ts）。
 *
 * 只是画面：窗口透明、点不到、没有预加载接口；淡出和关掉由主进程做。
 * 卡片上只有字标和一圈光（她冒出来的地方），没有文字，中英文界面都一样。
 */
import '../theme/brand.css'
</script>

<template>
  <div class="launch">
    <div class="card">
      <div class="mark">艾拉</div>
      <div class="sub">AILA</div>
      <div class="portal" aria-hidden="true"></div>
    </div>
  </div>
</template>

<style>
html,
body {
  margin: 0;
  background: transparent;
  overflow: hidden;
  user-select: none;
}
</style>

<style scoped>
.launch {
  display: grid;
  place-items: center;
  width: 100vw;
  height: 100vh;
}

.card {
  position: relative;
  width: 380px;
  height: 240px;
  border-radius: 28px;
  background: radial-gradient(circle at 50% 28%, #33285c 0%, #1a1430 58%, #110d1f 100%);
  box-shadow: 0 18px 48px rgba(10, 6, 24, 0.55), inset 0 0 0 1px rgba(196, 181, 253, 0.16);
  overflow: hidden;
  animation: card-in 0.45s cubic-bezier(0.2, 1.2, 0.4, 1) both;
}

/* 卡片上方一道斜光，慢慢扫过去 */
.card::before {
  content: '';
  position: absolute;
  inset: -40% -60%;
  background: linear-gradient(105deg, transparent 42%, rgba(196, 181, 253, 0.1) 50%, transparent 58%);
  animation: sweep 2.4s ease-in-out 0.3s both;
}

.mark {
  position: absolute;
  left: 0;
  right: 0;
  top: 34px;
  text-align: center;
  font-family: var(--ui-brand-font);
  font-size: 92px;
  line-height: 1;
  background: var(--ui-brand-text);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
  animation: ink 0.7s ease-out 0.1s both;
}

.sub {
  position: absolute;
  left: 0;
  right: 0;
  top: 138px;
  text-align: center;
  font: 600 11px/1 'Segoe UI', system-ui, sans-serif;
  letter-spacing: 0.6em;
  text-indent: 0.6em;
  color: rgba(196, 181, 253, 0.55);
  animation: fade 0.6s ease-out 0.35s both;
}

/* 她冒出来的地方：卡片下面一圈青紫色的光，一呼一吸 */
.portal {
  position: absolute;
  left: 50%;
  top: 173px; /* 中心对着窗口的 72% 高处：她的脚落在这里（shared/entrance.ts emergePosition） */
  width: 170px;
  height: 26px;
  margin-left: -85px;
  border-radius: 50%;
  background: radial-gradient(ellipse at center, rgba(103, 232, 249, 0.55) 0%, rgba(167, 139, 250, 0.35) 45%, transparent 72%);
  filter: blur(2px);
  animation: portal 1.4s ease-in-out 0.4s infinite alternate both;
}

@keyframes card-in {
  from {
    opacity: 0;
    transform: scale(0.9) translateY(10px);
  }
  to {
    opacity: 1;
    transform: none;
  }
}

@keyframes ink {
  from {
    opacity: 0;
    filter: blur(6px);
    transform: scale(1.06);
  }
  to {
    opacity: 1;
    filter: none;
    transform: none;
  }
}

@keyframes fade {
  from {
    opacity: 0;
  }
  to {
    opacity: 1;
  }
}

@keyframes sweep {
  from {
    transform: translateX(-30%);
  }
  to {
    transform: translateX(30%);
  }
}

@keyframes portal {
  from {
    opacity: 0.55;
    transform: scaleX(0.85);
  }
  to {
    opacity: 1;
    transform: scaleX(1.08);
  }
}
</style>
