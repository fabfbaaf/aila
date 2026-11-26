import type { KnowledgeDoc } from './cloud'

export interface ProjectDocuments {
  documents: KnowledgeDoc[]
  linked: string[]
}

interface Excerpt {
  id: string
  text: string
}

export interface DocumentSource extends Excerpt {
  document: string
  heading: string
  page: number | null
  version: string
}

export interface CodeSource extends Excerpt {
  file: string
  symbol: string
  start: number
  end: number
  depth: number
  from_id: string | null
  relation: 'symbol' | 'caller' | 'callee' | 'http' | 'ipc' | null
  via: string | null
}

export type EvidenceSource = DocumentSource | CodeSource

export interface ProjectAnswer {
  sources: EvidenceSource[]
  claims: { text: string; sources: string[] }[]
  gaps: string[]
  status: 'empty' | 'sources_only' | 'answered'
}
