/**
 * 从 MediaPipe 的 21 个手部关键点认手势（纯计算，不碰摄像头，可以单测）。
 *
 * 关键点编号：0 手腕；拇指 1–4；食指 5（根）6 7 8（指尖）；中指 9–12；无名指 13–16；小指 17–20。
 * 坐标是画面里的比例（0–1），z 不太准，所以只用 x、y。
 * 所有距离都除以「手的大小」（手腕到中指根），手离摄像头远近不影响判断。
 */

export interface Point {
  x: number
  y: number
  z?: number
}

export type Pose = 'open' | 'fist' | 'sword' | 'pinch' | 'point' | 'other'

export interface HandPose {
  pose: Pose
  /** 拇指、食指、中指、无名指、小指是不是伸直的 */
  extended: [boolean, boolean, boolean, boolean, boolean]
  /** 拇指尖到食指尖的距离（按手的大小算） */
  pinch: number
  /** 手的大小：手腕到中指根。越大说明手离摄像头越近（「往面前拉」就看它） */
  size: number
  /** 手掌中心、食指尖（已经左右镜像过：和屏幕上的方向一致） */
  palm: Point
  tip: Point
}

const PINCH = 0.28 // 拇指尖和食指尖靠到这么近算捏住
const TOGETHER = 0.3 // 剑指：食指和中指的指尖靠在一起

const dist = (a: Point, b: Point): number => Math.hypot(a.x - b.x, a.y - b.y)
const mirror = (p: Point): Point => ({ x: 1 - p.x, y: p.y })

export function classifyHand(lm: Point[]): HandPose {
  const size = Math.max(1e-4, dist(lm[0], lm[9]))
  const wrist = lm[0]
  // 四根手指：指尖离手腕比第二个关节远，就是伸直的
  const finger = (pip: number, tip: number): boolean => dist(wrist, lm[tip]) > dist(wrist, lm[pip]) * 1.15
  const index = finger(6, 8)
  const middle = finger(10, 12)
  const ring = finger(14, 16)
  const pinky = finger(18, 20)
  // 拇指：指尖离食指根够远就是张开的
  const thumb = dist(lm[4], lm[5]) / size > 0.55
  const pinch = dist(lm[4], lm[8]) / size

  let pose: Pose = 'other'
  if (!index && !middle && !ring && !pinky) pose = 'fist'
  else if (pinch < PINCH) pose = 'pinch'
  else if (index && middle && !ring && !pinky && dist(lm[8], lm[12]) / size < TOGETHER) pose = 'sword'
  else if (index && middle && ring && pinky) pose = 'open'
  else if (index && !middle && !ring && !pinky) pose = 'point'

  const palm = {
    x: (lm[0].x + lm[5].x + lm[9].x + lm[17].x) / 4,
    y: (lm[0].y + lm[5].y + lm[9].y + lm[17].y) / 4
  }
  return { pose, extended: [thumb, index, middle, ring, pinky], pinch, size, palm: mirror(palm), tip: mirror(lm[8]) }
}

/** 双手合十（结印）：两只手掌靠在一起、指尖也靠在一起，手指不是握拳的 */
export function isSeal(a: HandPose, b: HandPose): boolean {
  if (a.pose === 'fist' || b.pose === 'fist') return false
  const scale = (a.size + b.size) / 2
  return dist(a.palm, b.palm) / scale < 0.9 && dist(a.tip, b.tip) / scale < 0.6
}

/** 两只手掌之间的距离（按手的大小算）：拉开、合拢用来缩放 */
export function handsApart(a: HandPose, b: HandPose): number {
  return dist(a.palm, b.palm) / ((a.size + b.size) / 2)
}
