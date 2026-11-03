/**
 * 《千恋＊万花》共通 CHAPTER 2–3 的手工片段（依 `senren_ref/flowchart_spec.md` §2、§5、§9 與截圖
 * sb_27、sb_33、sb_38、sb_49～sb_51）。用來證明排版不依賴特定遊戲。
 * A hand-made Senren Banka fixture (common CH2–3) following the reference
 * spec and screenshots; proves the layout is game-independent.
 *
 * 涵蓋：2 個選項與 S 曲線合流（ココと都会）、3 個選項（どうする？）、跨好幾層的合流
 * （単独行動 → 部屋に戻って）、條件菱形接 3 條出邊、路線出口卡片。
 */
import type { StoryGraph } from '../core/types'

const n = (key: string, type: string, title: string | null, chapter: string, extra: object = {}) =>
  ({ key, tab: 'common', type, title, chapter, chapter_label: `CHAPTER ${chapter}`, line_count: 10, ...extra })

export const senrenCh3Fixture: StoryGraph = {
  schema_version: 1,
  tabs: [
    { key: 'common', title: '共通', order: 1, start: 'sb:hisashiburi' },
    { key: 'murasame', title: 'ムラサメ', order: 2, start: 'sb:saishidou' },
    { key: 'yoshino', title: '芳乃', order: 3, start: 'sb:yoshino' },
  ],
  nodes: [
    n('sb:hisashiburi', 'scene', '久しぶりの穂織', '2'),
    n('sb:gakuin', 'scene', '新たな学院で', '2'),
    n('sb:koko', 'choice', 'ココと都会とどっち？', '2'),
    n('sb:koko_a', 'option', '都会かな', '2'),
    n('sb:koko_b', 'option', 'どうだろ？', '2'),
    n('sb:gen', 'merge', '玄十郎の元へ', '2'),
    n('sb:doushiyou', 'choice', 'どうする？', '3'),
    n('sb:d_a', 'option', '一緒に行く', '3'),
    n('sb:d_a2', 'scene', '山道で', '3'),
    n('sb:d_a3', 'scene', '山頂', '3'),
    n('sb:d_b', 'option', '待っている', '3'),
    n('sb:d_c', 'option', '単独行動', '3'),
    n('sb:heya', 'merge', '部屋に戻って', '3'),
    n('sb:kakera', 'scene', '欠片集め', '3'),
    { key: 'sb:cond1', tab: 'common', type: 'condition', title: null, condition: '好感度', line_count: 0 },
    n('sb:oharai', 'merge', '2人でお祓い', '3'),
    n('sb:x_left', 'scene', '別の夜', '3'),
    n('sb:x_right', 'scene', '静かな朝', '3'),
    n('sb:mezame', 'scene', '目覚めると……', '3'),
    n('sb:fuan', 'choice', '不安げな朝武さんに…', '3'),
    n('sb:anshin', 'option', '安心させる', '3'),
    n('sb:mimamoru', 'option', '見守る', '3'),
    n('sb:mimamoru2', 'scene', '見守る（続）', '3'),
    n('sb:korekara', 'merge', 'これからについて', '3'),
    { key: 'sb:cond2', tab: 'common', type: 'condition', title: null, condition: '好感度', line_count: 0 },
    { key: 'sb:route:murasame', tab: 'common', type: 'route_exit', title: 'ムラサメ', target_tab: 'murasame', line_count: 0 },
    { key: 'sb:route:yoshino', tab: 'common', type: 'route_exit', title: '芳乃', target_tab: 'yoshino', line_count: 0 },
    { key: 'sb:route:mako', tab: 'common', type: 'route_exit', title: '茉子', target_tab: 'mako', line_count: 0 },
    { key: 'sb:saishidou', tab: 'murasame', type: 'scene', title: '再始動', chapter: '4', chapter_label: 'CHAPTER 4', line_count: 10 },
    { key: 'sb:mu_epi', tab: 'murasame', type: 'scene', title: 'Epilogue', chapter: 'epilogue', chapter_label: 'Epilogue', line_count: 10 },
    { key: 'sb:yoshino', tab: 'yoshino', type: 'scene', title: '芳乃と語らい', chapter: '4', chapter_label: 'CHAPTER 4', line_count: 10 },
  ],
  edges: [
    { from: 'sb:hisashiburi', to: 'sb:gakuin' },
    { from: 'sb:gakuin', to: 'sb:koko' },
    { from: 'sb:koko', to: 'sb:koko_a', kind: 'branch', order: 0, label: '都会かな' },
    { from: 'sb:koko', to: 'sb:koko_b', kind: 'branch', order: 1, label: 'どうだろう？' },
    { from: 'sb:koko_a', to: 'sb:gen', kind: 'merge' },
    { from: 'sb:koko_b', to: 'sb:gen', kind: 'merge' },
    { from: 'sb:gen', to: 'sb:doushiyou' },
    { from: 'sb:doushiyou', to: 'sb:d_a', kind: 'branch', order: 0 },
    { from: 'sb:doushiyou', to: 'sb:d_b', kind: 'branch', order: 1 },
    { from: 'sb:doushiyou', to: 'sb:d_c', kind: 'branch', order: 2 },
    { from: 'sb:d_a', to: 'sb:d_a2' },
    { from: 'sb:d_a2', to: 'sb:d_a3' },
    { from: 'sb:d_a3', to: 'sb:heya', kind: 'merge' },
    { from: 'sb:d_b', to: 'sb:heya', kind: 'merge' },
    { from: 'sb:d_c', to: 'sb:heya', kind: 'merge' },
    { from: 'sb:heya', to: 'sb:kakera' },
    { from: 'sb:kakera', to: 'sb:cond1' },
    { from: 'sb:cond1', to: 'sb:x_left', kind: 'branch', order: 0 },
    { from: 'sb:cond1', to: 'sb:oharai', kind: 'branch', order: 1 },
    { from: 'sb:cond1', to: 'sb:x_right', kind: 'branch', order: 2 },
    { from: 'sb:x_left', to: 'sb:oharai', kind: 'merge' },
    { from: 'sb:x_right', to: 'sb:oharai', kind: 'merge' },
    { from: 'sb:oharai', to: 'sb:mezame' },
    { from: 'sb:mezame', to: 'sb:fuan' },
    { from: 'sb:fuan', to: 'sb:anshin', kind: 'branch', order: 0 },
    { from: 'sb:fuan', to: 'sb:mimamoru', kind: 'branch', order: 1 },
    { from: 'sb:mimamoru', to: 'sb:mimamoru2' },
    { from: 'sb:anshin', to: 'sb:korekara', kind: 'merge' },
    { from: 'sb:mimamoru2', to: 'sb:korekara', kind: 'merge' },
    { from: 'sb:korekara', to: 'sb:cond2' },
    { from: 'sb:cond2', to: 'sb:route:murasame', kind: 'branch', order: 0 },
    { from: 'sb:cond2', to: 'sb:route:yoshino', kind: 'branch', order: 1 },
    { from: 'sb:cond2', to: 'sb:route:mako', kind: 'branch', order: 2 },
    { from: 'sb:saishidou', to: 'sb:mu_epi' },
  ],
} as StoryGraph
