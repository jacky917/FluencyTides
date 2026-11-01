import { describe, expect, it, vi } from 'vitest'
import { sabbatFixture } from '../fixtures/sabbat'
import { senrenCh3Fixture } from '../fixtures/senrenCh3'
import { pathTo, pathVariables, reachableInTab } from './path'

describe('pathTo（規則與後端 graph_index.py 相同）', () => {
  it('分歧點選 order 最小的入邊，並累加 effects', () => {
    const path = pathTo(sabbatFixture, 'sw:009_02B')!
    expect(path.nodes[0]).toBe('sw:001')
    expect(path.nodes.at(-1)).toBe('sw:009_02B')
    expect(path.nodes).toContain('sw:009#opt1-0')
    expect(pathVariables(path)).toMatchObject({ sel_flag: 1, meg_flag: 1 })
  })

  it('019 的第 4 個選項', () => {
    const path = pathTo(sabbatFixture, 'sw:019_02D')!
    expect(pathVariables(path).sel_flag).toBe(3)
    expect(path.edges.at(-1)!.order).toBe(3)
  })

  it('起點本身、走不到、不存在', () => {
    expect(pathTo(sabbatFixture, 'sw:001')).toEqual({ nodes: ['sw:001'], edges: [] })
    expect(pathTo(sabbatFixture, 'sw:nope')).toBeNull()
    expect(reachableInTab(senrenCh3Fixture, 'common').has('sb:route:mako')).toBe(true)
  })

  it('back 邊不參與路徑', () => {
    for (const e of pathTo(sabbatFixture, 'sw:117_03com')?.edges ?? []) expect(e.kind).not.toBe('back')
  })

  it('非數字的 add 略過並警告（與後端 path_variables 相同，不會變成 NaN）', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined)
    const vars = pathVariables({
      nodes: ['a', 'b', 'c', 'd'],
      edges: [
        { from: 'a', to: 'b', effects: [{ var: 'x', op: 'add', value: 2 }, { var: 's', op: 'set', value: 'on' }] },
        { from: 'b', to: 'c', effects: [{ var: 'x', op: 'add', value: '3' }, { var: 's', op: 'add', value: 1 }] },
        { from: 'c', to: 'd', effects: [{ var: 'x', op: 'add', value: 1 }] },
      ],
    })
    expect(vars).toEqual({ x: 3, s: 'on' })
    expect(warn).toHaveBeenCalledTimes(2)
    warn.mockRestore()
  })
})
