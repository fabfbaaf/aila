/**
 * 听到的话是不是她自己刚说的（回声没消干净）。语音打断时用来防止她被自己的声音打断。
 * 判断方法：去掉标点后，听到的字有六成以上出现在她正在说的话里。
 */
export function isEcho(heard: string, saying: string): boolean {
  const h = normalize(heard)
  const s = normalize(saying)
  if (h.length < 2 || s.length === 0) return false
  if (s.includes(h)) return true
  const pool = new Map<string, number>()
  for (const ch of s) pool.set(ch, (pool.get(ch) ?? 0) + 1)
  let hit = 0
  for (const ch of h) {
    const n = pool.get(ch) ?? 0
    if (n > 0) {
      hit++
      pool.set(ch, n - 1)
    }
  }
  return hit / h.length >= 0.6
}

function normalize(text: string): string {
  return text.toLowerCase().replace(/[^\p{L}\p{N}]/gu, '')
}
