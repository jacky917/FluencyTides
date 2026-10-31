import { describe, expect, it } from 'vitest'
import { graphHandlers, graphViewScope, nodeTooltip, pickGraphHandler } from './graphQuery'
import type { HandlerInfo } from '../types/api'

function handler(name: string, graph: boolean, links = false): HandlerInfo {
  const closed = { supported: false, reason: 'x' }
  return {
    name,
    title: name,
    note_types: [`${name}_type`],
    capabilities: {
      create: { ...closed, channels: [], flow: null, deck: null, fixed_deck: null, note_type: null },
      read: { supported: true, reason: null },
      update_fields: closed,
      delete: closed,
      actions: [],
      graph: graph ? { supported: true, reason: null, links } : { ...closed, links: false },
    },
    create_schema: null,
    json_fields: [],
    media_fields: [],
  }
}

describe('graph handler choice', () => {
  const handlers = [handler('speaking_coach', false), handler('vocabulary_mining', true, true), handler('other', true)]

  it('lists only handlers offering a graph', () => {
    expect(graphHandlers(handlers).map((h) => h.name)).toEqual(['vocabulary_mining', 'other'])
    expect(graphHandlers(undefined)).toEqual([])
  })

  it('keeps the remembered handler while it still offers a graph', () => {
    const available = graphHandlers(handlers)
    expect(pickGraphHandler(available, 'other')?.name).toBe('other')
    expect(pickGraphHandler(available, 'speaking_coach')?.name).toBe('vocabulary_mining')
    expect(pickGraphHandler(available, null)?.name).toBe('vocabulary_mining')
    expect(pickGraphHandler([], 'other')).toBeUndefined()
  })
})

describe('graphViewScope', () => {
  it('keeps the old deck key for the vocabulary graph without a note type filter', () => {
    expect(graphViewScope('vocabulary_mining', 'English', null)).toBe('English')
  })

  it('separates other handlers and note types', () => {
    expect(graphViewScope('vocabulary_mining', 'English', 'TOEIC_Coach_Dark_v2')).toBe(
      'vocabulary_mining|TOEIC_Coach_Dark_v2|English',
    )
    expect(graphViewScope('expression_correction', 'All Decks', null)).toBe('expression_correction|*|All Decks')
  })
})

describe('nodeTooltip', () => {
  it('shows status only on relation graphs, and marks ghost nodes', () => {
    expect(nodeTooltip({ id: 'anyway', label: 'anyway', translation: '無論如何', status: 'review' }, true)).toBe(
      'anyway (無論如何) - review',
    )
    expect(nodeTooltip({ id: 'anyhow', label: 'anyhow' }, true)).toBe('anyhow - 未建卡')
    expect(nodeTooltip({ id: '1712', label: '1712', note_id: 1712 }, false)).toBe('1712')
    expect(nodeTooltip({ id: '5' }, false)).toBe('5')
  })
})
