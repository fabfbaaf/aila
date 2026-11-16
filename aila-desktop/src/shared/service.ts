/** AI 服务的托管状态：主进程、预加载和设置页共用，不带环境变量或启动配置。 */
export type ServicePhase = 'checking' | 'external' | 'starting' | 'ready' | 'crashed' | 'failed' | 'missing' | 'stopped'

export interface ServiceState {
  phase: ServicePhase
  pid: number | null
  error: string
}

export interface ServiceLog {
  time: string
  text: string
}

export interface ServiceResult {
  ok: boolean
  error?: string
}
