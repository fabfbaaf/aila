/**
 * 摄像头 + MediaPipe 手部关键点：每一帧给出画面里的手（最多两只）。
 *
 * - 全在本机算：模型和 wasm 放在 public/mediapipe（npm run setup:gestures 准备），不连任何服务器，画面不保存、不上传
 * - 优先用 GPU，不行再退到 CPU
 * - 窗口看不见的时候不识别（省电），关掉时把摄像头彻底释放（系统的摄像头指示灯会灭）
 */
import { FilesetResolver, HandLandmarker } from '@mediapipe/tasks-vision'
import type { Point } from './classify'

export type TrackerError = 'denied' | 'no-camera' | 'busy' | 'model' | 'unknown'

export class TrackerStartError extends Error {
  constructor(
    readonly code: TrackerError,
    message: string
  ) {
    super(message)
  }
}

const MESSAGES: Record<TrackerError, string> = {
  denied: '摄像头没被允许使用：在 Windows「设置 → 隐私和安全性 → 摄像头」里允许桌面应用使用摄像头',
  'no-camera': '没找到摄像头',
  busy: '摄像头被别的程序占着（关掉视频会议之类的软件再试）',
  model: '手势模型没装好：在 aila-desktop 目录运行 npm run setup:gestures',
  unknown: '摄像头打不开'
}

/** 每秒最多识别这么多次：够跟手了，也不太占电脑 */
const MAX_FPS = 30

export class HandTracker {
  /** 每识别一帧回调一次：hands 是每只手的 21 个关键点（画面比例坐标，没镜像） */
  onHands: (hands: Point[][], now: number) => void = () => {}

  readonly video = document.createElement('video')
  private stream: MediaStream | null = null
  private landmarker: HandLandmarker | null = null
  private frame = 0
  private lastVideoTime = -1
  private lastRun = 0
  private stopped = false

  async start(): Promise<void> {
    this.stopped = false
    const base = new URL('mediapipe/', location.href.split('#')[0]).href
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        video: { width: { ideal: 640 }, height: { ideal: 480 }, facingMode: 'user' },
        audio: false
      })
    } catch (err) {
      throw cameraError(err)
    }
    if (this.stopped) return this.release()
    this.video.srcObject = this.stream
    this.video.muted = true
    this.video.playsInline = true
    await this.video.play()

    try {
      this.landmarker = await createLandmarker(base)
    } catch (err) {
      this.release()
      console.warn('[gestures] 模型加载失败', err)
      throw new TrackerStartError('model', MESSAGES.model)
    }
    if (this.stopped) return this.release()
    this.frame = requestAnimationFrame(this.loop)
  }

  stop(): void {
    this.stopped = true
    this.release()
  }

  private release(): void {
    cancelAnimationFrame(this.frame)
    this.stream?.getTracks().forEach((t) => t.stop())
    this.stream = null
    this.video.srcObject = null
    this.landmarker?.close()
    this.landmarker = null
  }

  private loop = (): void => {
    this.frame = requestAnimationFrame(this.loop)
    const now = performance.now()
    if (!this.landmarker || document.hidden || now - this.lastRun < 1000 / MAX_FPS) return
    if (this.video.readyState < 2 || this.video.currentTime === this.lastVideoTime) return
    this.lastVideoTime = this.video.currentTime
    this.lastRun = now
    const result = this.landmarker.detectForVideo(this.video, now)
    this.onHands(result.landmarks ?? [], now)
  }
}

async function createLandmarker(base: string): Promise<HandLandmarker> {
  const fileset = await FilesetResolver.forVisionTasks(base)
  const options = (delegate: 'GPU' | 'CPU') => ({
    baseOptions: { modelAssetPath: `${base}hand_landmarker.task`, delegate },
    runningMode: 'VIDEO' as const,
    numHands: 2,
    minHandDetectionConfidence: 0.6,
    minHandPresenceConfidence: 0.6,
    minTrackingConfidence: 0.5
  })
  try {
    return await HandLandmarker.createFromOptions(fileset, options('GPU'))
  } catch (err) {
    console.warn('[gestures] GPU 不可用，改用 CPU', err)
    return await HandLandmarker.createFromOptions(fileset, options('CPU'))
  }
}

function cameraError(err: unknown): TrackerStartError {
  const name = (err as DOMException)?.name
  const code: TrackerError =
    name === 'NotAllowedError' || name === 'SecurityError'
      ? 'denied'
      : name === 'NotFoundError' || name === 'OverconstrainedError'
        ? 'no-camera'
        : name === 'NotReadableError' || name === 'AbortError'
          ? 'busy'
          : 'unknown'
  return new TrackerStartError(code, MESSAGES[code])
}

/** 手的骨架连线（画在摄像头预览上） */
export const HAND_BONES: [number, number][] = [
  [0, 1], [1, 2], [2, 3], [3, 4],
  [0, 5], [5, 6], [6, 7], [7, 8],
  [5, 9], [9, 10], [10, 11], [11, 12],
  [9, 13], [13, 14], [14, 15], [15, 16],
  [13, 17], [17, 18], [18, 19], [19, 20],
  [0, 17]
]
