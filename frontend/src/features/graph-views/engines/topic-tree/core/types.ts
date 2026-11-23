/**
 * 主題樹引擎吃的資料（後端 `topic_tree.build_graph` 的形狀）。引擎不 import 網站的型別，
 * 欄位與 `GET /handlers/{name}/graph` 的樹狀圖譜相同，網站的 `GraphData` 可以直接傳進來。
 *
 * The data the topic tree engine takes: the shape of the backend's tree graph. The engine does not import the
 * site's types; the site's `GraphData` is assignable to it.
 */

/** 主題樹的一個節點。One node of the topic tree. */
export interface TopicTreeNode {
  id: string
  label?: string
  /** `root` 根、`card` 卡片、`deck` 沒有對應卡的子牌組。The node kind. */
  kind?: 'root' | 'card' | 'deck'
  /** 卡片所在的牌組，或牌組節點本身。The card's deck, or the deck itself. */
  deck?: string | null
  /** 點了要進入的子牌組；沒有子節點時為 null。The deck to enter, or null. */
  child_deck?: string | null
  /** 直接在它底下的節點數（收起時也有）。Direct children, also when folded. */
  child_count?: number
  /** 第幾層（根為 0）。The level, root being 0. */
  depth?: number
  /** 卡片的 Note ID。The card's note ID. */
  note_id?: number | null
  /** 卡片可用的標題。The card's usable title. */
  title?: string | null
  /** 卡片的情境提示。The card's prompt. */
  prompt?: string
  /** 同一層有標題相同的卡，這張不擁有子牌組。Shares its title with an earlier card. */
  duplicate_title?: boolean
}

/** 父節點指向子節點的連線。A parent-to-child link. */
export interface TopicTreeLink {
  source: string
  target: string
  label?: string
}

/** 主題樹的資料。The topic tree data. */
export interface TopicTreeData {
  nodes: TopicTreeNode[]
  links: TopicTreeLink[]
}
