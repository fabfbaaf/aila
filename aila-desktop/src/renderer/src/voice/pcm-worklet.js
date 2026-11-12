// 在音频线程里运行：把麦克风的 float32 采样转成 16 位整数，攒够 100ms 发给主线程。
// AudioContext 本身是 16kHz，重采样由浏览器完成。
// 用普通 JS 写，构建时原样复制，不经过 TypeScript。

const CHUNK = 1600 // 16000Hz × 0.1s

class Pcm16Sender extends AudioWorkletProcessor {
  constructor() {
    super()
    this.buf = new Int16Array(CHUNK)
    this.n = 0
  }

  process(inputs) {
    const ch = inputs[0] && inputs[0][0]
    if (!ch) return true
    for (let i = 0; i < ch.length; i++) {
      const s = Math.max(-1, Math.min(1, ch[i]))
      this.buf[this.n++] = s < 0 ? s * 0x8000 : s * 0x7fff
      if (this.n === CHUNK) {
        this.port.postMessage(this.buf.buffer, [this.buf.buffer])
        this.buf = new Int16Array(CHUNK)
        this.n = 0
      }
    }
    return true
  }
}

registerProcessor('pcm16-sender', Pcm16Sender)
