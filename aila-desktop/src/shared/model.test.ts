import { describe, expect, it } from 'vitest'
import { modelKind, relativeModelPath } from './model'

describe('形象文件', () => {
  it('按文件名认 VRM 和 Live2D', () => {
    expect(modelKind('C:/m/aila.VRM')).toBe('vrm')
    expect(modelKind('D:\\live2d\\hiyori\\hiyori_pro_t11.model3.json')).toBe('live2d')
    expect(modelKind('D:/x/model.json')).toBeNull() // Cubism 2 的旧格式不认
    expect(modelKind('evil.exe')).toBeNull()
  })

  it('模型文件夹里的相对路径：带上文件夹名、统一用 /、不许跑出去', () => {
    expect(relativeModelPath('hiyori', 'textures\\texture_00.png')).toBe('hiyori/textures/texture_00.png')
    expect(relativeModelPath('hiyori', 'hiyori.moc3')).toBe('hiyori/hiyori.moc3')
    expect(relativeModelPath('hiyori', '../secret.txt')).toBeNull()
    expect(relativeModelPath('hiyori', '')).toBeNull()
  })
})
