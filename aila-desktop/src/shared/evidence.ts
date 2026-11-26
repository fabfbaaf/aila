import type { KnowledgeDoc } from './cloud'

export interface ProjectDocuments {
  documents: KnowledgeDoc[]
  linked: string[]
}

interface Excerpt {
  id: string
  text: string
  matches?: string[]
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
  relation: 'symbol' | 'caller' | 'callee' | 'http' | 'ipc' | 'test' | 'test_reference' | 'reference' | null
  via: string | null
}

export interface DecisionSource extends Excerpt {
  decision_id: string
  effective: number
  until: number | null
  supersedes: string | null
  session: string | null
  message_seq: number | null
  message: string | null
}

export type EvidenceSource = DocumentSource | CodeSource | DecisionSource

export interface ProjectEvidenceQuery {
  query: string
  mode: 'answer' | 'inspect'
  memory: 'current' | 'history'
  as_of?: number
}

export interface ProjectAnswer {
  sources: EvidenceSource[]
  claims: { text: string; sources: string[]; kind: 'supported' | 'inference'; quotes: Record<string, string> }[]
  gaps: string[]
  status: 'empty' | 'sources_only' | 'answered' | 'inspection'
  sent: boolean
  retrieval: {
    document_candidates: number
    code_candidates: number
    reranked: ('documents' | 'code')[]
    excluded: EvidenceSource[]
  }
}
