/**
 * Card Generator 頁面（handlers 計畫書 §6、D9、D13）。
 * Card Generator page.
 *
 * 版面與 Cards 相同：牌組樹（並排時在左、窄螢幕時在上方）決定新卡建在哪個牌組，選「全部牌組」
 * 或已不存在的牌組時無法建立；另一側是建卡表單。
 *
 * 卡片類型選單與表單（填表、預覽兩種流程，草稿保存、送出、錯誤顯示）在
 * `components/generate/CreatePanelBody.tsx`，卡片瀏覽頁的建卡畫面也用它。
 * - 填表流程（`flow="form"`）：表單由 `create_schema` 產生（`lib/createForm.ts`），不呼叫 LLM。
 * - 預覽流程（`flow="preview"`，目前只有三語對話卡）：先以 `draft_schema` 的表單請 LLM 生成草稿，勾選後
 *   建立或廢棄（`components/generate/PreviewCreate.tsx`）。
 *
 * The same layout as Cards. Handlers appear per their declared channels; the
 * form comes from `create_schema`; field errors are shown next to fields.
 */

import { PlusCircle } from 'lucide-react'
import { CreatePanelBody } from '../components/generate/CreatePanelBody'
import { DeckTreePanel } from '../components/shared/DeckTreePanel'
import { PageHeader } from '../components/shared/PageHeader'
import { Panel } from '../components/shared/Panel'
import { SplitLayout } from '../components/shared/SplitLayout'
import { useDeckStatus } from '../hooks/useDeckStatus'
import { usePanelWidths } from '../hooks/usePanelWidths'
import { useLocalStorage } from '../hooks/useStoredState'
import { GENERATE_LAYOUT } from '../lib/pageLayouts'

/**
 * 卡片產生器頁面：在牌組樹選牌組，再選卡片類型、填表建立。
 * Card generator page: pick the deck, then a card type, and fill in the form.
 * @returns 卡片產生器頁面 JSX。Card generator page JSX.
 */
export default function CardGenerator() {
  const [selectedDeck, setSelectedDeck] = useLocalStorage<string | null>(
    'generate_selectedDeck',
    import.meta.env.VITE_DEFAULT_DECK || null,
  )
  const [deckToggled, setDeckToggled] = useLocalStorage<Record<string, boolean>>('generate_deck_toggled', {})
  const panels = usePanelWidths(GENERATE_LAYOUT)
  // 記住的牌組可能已在 Anki 被刪除或改名，後端遇到不存在的牌組會自動建立，所以要確認牌組還在才能
  // 建立；清單還沒載入或載入失敗時無法確認，也不能建立
  const deckStatus = useDeckStatus(selectedDeck)

  const formPanel = (sideBySide: boolean) => (
    <Panel
      scrollable={sideBySide}
      title={
        <>
          <PlusCircle className="h-4 w-4" />
          建立卡片
        </>
      }
      contentClassName="space-y-6"
    >
      <CreatePanelBody selectedDeck={selectedDeck} deckStatus={deckStatus} />
    </Panel>
  )

  return (
    <SplitLayout
      header={
        <PageHeader
          title="Card Generator"
          description="在牌組樹選牌組，再選卡片類型建立。三語對話卡、外語糾錯由 LLM 生成、預覽勾選後建立；其他卡片手動填表。"
        />
      }
      panels={panels}
      columns={[
        {
          id: 'deck',
          resizable: 'deck',
          render: (sideBySide) => (
            <DeckTreePanel
              scrollable={sideBySide}
              toggled={deckToggled}
              onToggled={setDeckToggled}
              selected={selectedDeck}
              onSelect={setSelectedDeck}
            />
          ),
        },
        { id: 'main', render: formPanel },
      ]}
    />
  )
}
