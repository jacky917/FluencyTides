/**
 * 《魔女的夜宴》的劇情圖片段（共通、寧々、和奏三個分頁），取自 2026-11-01 的建置包，
 * 去掉了 meta。測試與 `/story/_playground` 用。
 * A Sabbat of the Witch fixture (common, Nene, Wakana tabs) from a real build.
 */
import type { StoryGraph } from '../core/types'

export const sabbatFixture: StoryGraph = {
 "schema_version": 1,
 "tabs": [
  {
   "key": "common",
   "title": "共通",
   "order": 1,
   "start": "sw:001"
  },
  {
   "key": "nene",
   "title": "寧々",
   "order": 2,
   "start": "sw:101",
   "avatar": "nene"
  },
  {
   "key": "wakana",
   "title": "和奏",
   "order": 6,
   "start": "sw:500",
   "avatar": "wakana"
  }
 ],
 "nodes": [
  {
   "key": "sw:001",
   "tab": "common",
   "type": "scene",
   "title": "オナニーマスター",
   "chapter": "1",
   "chapter_label": "CHAPTER 1",
   "line_count": 635
  },
  {
   "key": "sw:002",
   "tab": "common",
   "type": "scene",
   "title": "欠片吸収",
   "chapter": "1",
   "chapter_label": "CHAPTER 1",
   "line_count": 665
  },
  {
   "key": "sw:003",
   "tab": "common",
   "type": "scene",
   "title": "心の穴",
   "chapter": "1",
   "chapter_label": "CHAPTER 1",
   "line_count": 522
  },
  {
   "key": "sw:004",
   "tab": "common",
   "type": "scene",
   "title": "今後もよろしく",
   "chapter": "1",
   "chapter_label": "CHAPTER 1",
   "line_count": 83
  },
  {
   "key": "sw:005",
   "tab": "common",
   "type": "choice",
   "title": "因幡めぐる",
   "chapter": "2",
   "chapter_label": "CHAPTER 2",
   "line_count": 433
  },
  {
   "key": "sw:005#opt1-1",
   "tab": "common",
   "type": "option",
   "title": "しつこいからもういいか",
   "line_count": 0
  },
  {
   "key": "sw:005_01A",
   "tab": "common",
   "type": "option",
   "title": "もう１回正直に褒める",
   "chapter": "2",
   "chapter_label": "CHAPTER 2",
   "line_count": 3
  },
  {
   "key": "sw:005_01com",
   "tab": "common",
   "type": "merge",
   "title": "因幡めぐる（続）",
   "chapter": "2",
   "chapter_label": "CHAPTER 2",
   "line_count": 250
  },
  {
   "key": "sw:006",
   "tab": "common",
   "type": "choice",
   "title": "合コンテク",
   "chapter": "2",
   "chapter_label": "CHAPTER 2",
   "line_count": 275
  },
  {
   "key": "sw:006_01A",
   "tab": "common",
   "type": "option",
   "title": "綾地さんに頼む",
   "chapter": "2",
   "chapter_label": "CHAPTER 2",
   "line_count": 127
  },
  {
   "key": "sw:006_01com",
   "tab": "common",
   "type": "merge",
   "title": "合コンテク（続）",
   "chapter": "2",
   "chapter_label": "CHAPTER 2",
   "line_count": 51
  },
  {
   "key": "sw:006_01B",
   "tab": "common",
   "type": "option",
   "title": "このまま因幡さんと続ける",
   "chapter": "2",
   "chapter_label": "CHAPTER 2",
   "line_count": 64
  },
  {
   "key": "sw:007",
   "tab": "common",
   "type": "scene",
   "title": "めぐるのその後",
   "chapter": "2",
   "chapter_label": "CHAPTER 2",
   "line_count": 252
  },
  {
   "key": "sw:008",
   "tab": "common",
   "type": "scene",
   "title": "デート依頼",
   "chapter": "3",
   "chapter_label": "CHAPTER 3",
   "line_count": 379
  },
  {
   "key": "sw:009",
   "tab": "common",
   "type": "choice",
   "title": "休日デート①",
   "chapter": "3",
   "chapter_label": "CHAPTER 3",
   "line_count": 141
  },
  {
   "key": "sw:009#opt1-0",
   "tab": "common",
   "type": "option",
   "title": "……悩む",
   "line_count": 0
  },
  {
   "key": "sw:009#opt1-1",
   "tab": "common",
   "type": "option",
   "title": "時間を変更する",
   "line_count": 0
  },
  {
   "key": "sw:009_01A",
   "tab": "common",
   "type": "choice",
   "title": "休日デート②",
   "chapter": "3",
   "chapter_label": "CHAPTER 3",
   "line_count": 87
  },
  {
   "key": "sw:009_02A",
   "tab": "common",
   "type": "option",
   "title": "いや、普通に映画に集中",
   "chapter": "3",
   "chapter_label": "CHAPTER 3",
   "line_count": 11
  },
  {
   "key": "sw:009_01com",
   "tab": "common",
   "type": "choice",
   "title": "休日デート③",
   "chapter": "3",
   "chapter_label": "CHAPTER 3",
   "line_count": 80
  },
  {
   "key": "sw:009_04A",
   "tab": "common",
   "type": "option",
   "title": "かもね",
   "chapter": "3",
   "chapter_label": "CHAPTER 3",
   "line_count": 4
  },
  {
   "key": "sw:009_04com",
   "tab": "common",
   "type": "merge",
   "title": "休日デート（続）",
   "chapter": "3",
   "chapter_label": "CHAPTER 3",
   "line_count": 116
  },
  {
   "key": "sw:009_04B",
   "tab": "common",
   "type": "option",
   "title": "別に",
   "chapter": "3",
   "chapter_label": "CHAPTER 3",
   "line_count": 7
  },
  {
   "key": "sw:009_02B",
   "tab": "common",
   "type": "option",
   "title": "……これもリハーサル",
   "chapter": "3",
   "chapter_label": "CHAPTER 3",
   "line_count": 43
  },
  {
   "key": "sw:009_01B",
   "tab": "common",
   "type": "choice",
   "title": "休日デート④",
   "chapter": "3",
   "chapter_label": "CHAPTER 3",
   "line_count": 33
  },
  {
   "key": "sw:009_03A",
   "tab": "common",
   "type": "option",
   "title": "このままみんなで移動",
   "chapter": "3",
   "chapter_label": "CHAPTER 3",
   "line_count": 38
  },
  {
   "key": "sw:009_03B",
   "tab": "common",
   "type": "option",
   "title": "空腹を解消したい",
   "chapter": "3",
   "chapter_label": "CHAPTER 3",
   "line_count": 63
  },
  {
   "key": "sw:009_03B#cond",
   "tab": "common",
   "type": "condition",
   "line_count": 0,
   "condition": "依變數決定：wak_flag"
  },
  {
   "key": "sw:009_wak_com",
   "tab": "common",
   "type": "merge",
   "title": "休日デート（続）",
   "chapter": "3",
   "chapter_label": "CHAPTER 3",
   "line_count": 6
  },
  {
   "key": "sw:009_dummy1",
   "tab": "common",
   "type": "scene",
   "title": "休日デート⑤",
   "chapter": "3",
   "chapter_label": "CHAPTER 3",
   "line_count": 63
  },
  {
   "key": "sw:010",
   "tab": "common",
   "type": "choice",
   "title": "転入生",
   "chapter": "3",
   "chapter_label": "CHAPTER 3",
   "line_count": 569
  },
  {
   "key": "sw:010_01A",
   "tab": "common",
   "type": "option",
   "title": "ちょっとじゃれてみる",
   "chapter": "3",
   "chapter_label": "CHAPTER 3",
   "line_count": 8
  },
  {
   "key": "sw:010_01com",
   "tab": "common",
   "type": "merge",
   "title": "転入生（続）",
   "chapter": "3",
   "chapter_label": "CHAPTER 3",
   "line_count": 111
  },
  {
   "key": "sw:010_01B",
   "tab": "common",
   "type": "option",
   "title": "普通に言う",
   "chapter": "3",
   "chapter_label": "CHAPTER 3",
   "line_count": 2
  },
  {
   "key": "sw:011",
   "tab": "common",
   "type": "scene",
   "title": "先輩の悩み",
   "chapter": "3",
   "chapter_label": "CHAPTER 3",
   "line_count": 455
  },
  {
   "key": "sw:012",
   "tab": "common",
   "type": "scene",
   "title": "魔女",
   "chapter": "3",
   "chapter_label": "CHAPTER 3",
   "line_count": 441
  },
  {
   "key": "sw:013",
   "tab": "common",
   "type": "scene",
   "title": "キマシタワ",
   "chapter": "3",
   "chapter_label": "CHAPTER 3",
   "line_count": 382
  },
  {
   "key": "sw:014",
   "tab": "common",
   "type": "choice",
   "title": "失恋",
   "chapter": "3",
   "chapter_label": "CHAPTER 3",
   "line_count": 61
  },
  {
   "key": "sw:014_01A",
   "tab": "common",
   "type": "option",
   "title": "誤魔化す",
   "chapter": "3",
   "chapter_label": "CHAPTER 3",
   "line_count": 8
  },
  {
   "key": "sw:014_01com",
   "tab": "common",
   "type": "merge",
   "title": "失恋（続）",
   "chapter": "3",
   "chapter_label": "CHAPTER 3",
   "line_count": 246
  },
  {
   "key": "sw:014_01B",
   "tab": "common",
   "type": "option",
   "title": "正直に言う",
   "chapter": "3",
   "chapter_label": "CHAPTER 3",
   "line_count": 14
  },
  {
   "key": "sw:015",
   "tab": "common",
   "type": "scene",
   "title": "釣り①",
   "chapter": "3",
   "chapter_label": "CHAPTER 3",
   "line_count": 102
  },
  {
   "key": "sw:015#cond",
   "tab": "common",
   "type": "condition",
   "line_count": 0,
   "condition": "依變數決定：nen_flag"
  },
  {
   "key": "sw:015_01A",
   "tab": "common",
   "type": "scene",
   "title": "釣り②",
   "chapter": "3",
   "chapter_label": "CHAPTER 3",
   "line_count": 4
  },
  {
   "key": "sw:015_01B",
   "tab": "common",
   "type": "scene",
   "title": "釣り③",
   "chapter": "3",
   "chapter_label": "CHAPTER 3",
   "line_count": 3
  },
  {
   "key": "sw:015_01com",
   "tab": "common",
   "type": "merge",
   "title": "釣り（続）",
   "chapter": "3",
   "chapter_label": "CHAPTER 3",
   "line_count": 344
  },
  {
   "key": "sw:016",
   "tab": "common",
   "type": "scene",
   "title": "廃部",
   "chapter": "4",
   "chapter_label": "CHAPTER 4",
   "line_count": 385
  },
  {
   "key": "sw:017",
   "tab": "common",
   "type": "choice",
   "title": "勉強会",
   "chapter": "4",
   "chapter_label": "CHAPTER 4",
   "line_count": 109
  },
  {
   "key": "sw:017_01A",
   "tab": "common",
   "type": "option",
   "title": "諦めて気分を変える",
   "chapter": "4",
   "chapter_label": "CHAPTER 4",
   "line_count": 71
  },
  {
   "key": "sw:017_01com",
   "tab": "common",
   "type": "merge",
   "title": "勉強会（続）",
   "chapter": "4",
   "chapter_label": "CHAPTER 4",
   "line_count": 248
  },
  {
   "key": "sw:017_01B",
   "tab": "common",
   "type": "option",
   "title": "いい機会だから教えてもらう",
   "chapter": "4",
   "chapter_label": "CHAPTER 4",
   "line_count": 90
  },
  {
   "key": "sw:018",
   "tab": "common",
   "type": "choice",
   "title": "パーティー準備",
   "chapter": "4",
   "chapter_label": "CHAPTER 4",
   "line_count": 628
  },
  {
   "key": "sw:018_01A",
   "tab": "common",
   "type": "option",
   "title": "もう一度握ってもらう",
   "chapter": "4",
   "chapter_label": "CHAPTER 4",
   "line_count": 20
  },
  {
   "key": "sw:018_01com",
   "tab": "common",
   "type": "merge",
   "title": "パーティー準備（続）",
   "chapter": "4",
   "chapter_label": "CHAPTER 4",
   "line_count": 8
  },
  {
   "key": "sw:018_01com#unlock",
   "tab": "common",
   "type": "condition",
   "line_count": 0,
   "condition": "任一路線通關後"
  },
  {
   "key": "sw:route:wakana",
   "tab": "common",
   "type": "route_exit",
   "title": "和奏",
   "line_count": 0,
   "target_tab": "wakana"
  },
  {
   "key": "sw:018_01B",
   "tab": "common",
   "type": "option",
   "title": "さすがにそんなことは言えない",
   "chapter": "4",
   "chapter_label": "CHAPTER 4",
   "line_count": 5
  },
  {
   "key": "sw:019",
   "tab": "common",
   "type": "choice",
   "title": "パーティ①",
   "chapter": "4",
   "chapter_label": "CHAPTER 4",
   "line_count": 162
  },
  {
   "key": "sw:019_01A",
   "tab": "common",
   "type": "option",
   "title": "褒める",
   "chapter": "4",
   "chapter_label": "CHAPTER 4",
   "line_count": 11
  },
  {
   "key": "sw:019_01B",
   "tab": "common",
   "type": "option",
   "title": "目をそらす",
   "chapter": "4",
   "chapter_label": "CHAPTER 4",
   "line_count": 6
  },
  {
   "key": "sw:019_01com",
   "tab": "common",
   "type": "choice",
   "title": "パーティ②",
   "chapter": "4",
   "chapter_label": "CHAPTER 4",
   "line_count": 14
  },
  {
   "key": "sw:019_02A",
   "tab": "common",
   "type": "option",
   "title": "因幡さん、かな",
   "chapter": "4",
   "chapter_label": "CHAPTER 4",
   "line_count": 3
  },
  {
   "key": "sw:019_02com",
   "tab": "common",
   "type": "merge",
   "title": "パーティ（続）",
   "chapter": "4",
   "chapter_label": "CHAPTER 4",
   "line_count": 266
  },
  {
   "key": "sw:019_02com#cond",
   "tab": "common",
   "type": "condition",
   "line_count": 0,
   "condition": "依變數決定：meg_flag, nen_flag, tou_flag, tsu_flag"
  },
  {
   "key": "sw:019_normal_end",
   "tab": "common",
   "type": "ending",
   "title": "ノーマルエンド",
   "chapter": "4",
   "chapter_label": "CHAPTER 4",
   "line_count": 14
  },
  {
   "key": "sw:019_02B",
   "tab": "common",
   "type": "option",
   "title": "綾地さんがいいかな",
   "chapter": "4",
   "chapter_label": "CHAPTER 4",
   "line_count": 4
  },
  {
   "key": "sw:019_02C",
   "tab": "common",
   "type": "option",
   "title": "やっぱり……戸隠先輩？",
   "chapter": "4",
   "chapter_label": "CHAPTER 4",
   "line_count": 15
  },
  {
   "key": "sw:019_02D",
   "tab": "common",
   "type": "option",
   "title": "今はバンドの事で頭がいっぱい",
   "chapter": "4",
   "chapter_label": "CHAPTER 4",
   "line_count": 7
  },
  {
   "key": "sw:020.共通－パーティーめぐる",
   "tab": "common",
   "type": "scene",
   "title": "パーティーめぐる",
   "chapter": "4",
   "chapter_label": "CHAPTER 4",
   "line_count": 113
  },
  {
   "key": "sw:route:meguru",
   "tab": "common",
   "type": "route_exit",
   "title": "めぐる",
   "line_count": 0,
   "target_tab": "meguru"
  },
  {
   "key": "sw:020.共通－パーティー紬",
   "tab": "common",
   "type": "scene",
   "title": "パーティー紬",
   "chapter": "4",
   "chapter_label": "CHAPTER 4",
   "line_count": 134
  },
  {
   "key": "sw:route:tsumugi",
   "tab": "common",
   "type": "route_exit",
   "title": "紬",
   "line_count": 0,
   "target_tab": "tsumugi"
  },
  {
   "key": "sw:020.共通－パーティ寧々",
   "tab": "common",
   "type": "scene",
   "title": "パーティ寧々",
   "chapter": "4",
   "chapter_label": "CHAPTER 4",
   "line_count": 201
  },
  {
   "key": "sw:route:nene",
   "tab": "common",
   "type": "route_exit",
   "title": "寧々",
   "line_count": 0,
   "target_tab": "nene"
  },
  {
   "key": "sw:020.共通－パーティ憧子",
   "tab": "common",
   "type": "scene",
   "title": "パーティ憧子",
   "chapter": "4",
   "chapter_label": "CHAPTER 4",
   "line_count": 109
  },
  {
   "key": "sw:route:touko",
   "tab": "common",
   "type": "route_exit",
   "title": "憧子",
   "line_count": 0,
   "target_tab": "touko"
  },
  {
   "key": "sw:101",
   "tab": "nene",
   "type": "scene",
   "title": "ラーメン",
   "chapter": "5",
   "chapter_label": "CHAPTER 5",
   "line_count": 563
  },
  {
   "key": "sw:102",
   "tab": "nene",
   "type": "scene",
   "title": "お見舞い",
   "chapter": "5",
   "chapter_label": "CHAPTER 5",
   "line_count": 550
  },
  {
   "key": "sw:103",
   "tab": "nene",
   "type": "scene",
   "title": "告白",
   "chapter": "5",
   "chapter_label": "CHAPTER 5",
   "line_count": 1542
  },
  {
   "key": "sw:104",
   "tab": "nene",
   "type": "choice",
   "title": "初体験①",
   "chapter": "5",
   "chapter_label": "CHAPTER 5",
   "line_count": 1219
  },
  {
   "key": "sw:104#opt1-0",
   "tab": "nene",
   "type": "option",
   "title": "このまま射精したい",
   "line_count": 0
  },
  {
   "key": "sw:104#opt1-1",
   "tab": "nene",
   "type": "option",
   "title": "身体を汚したい",
   "line_count": 0
  },
  {
   "key": "sw:104_dummy1",
   "tab": "nene",
   "type": "merge",
   "title": "初体験（続）",
   "chapter": "5",
   "chapter_label": "CHAPTER 5",
   "line_count": 7
  },
  {
   "key": "sw:104_dummy1#cond",
   "tab": "nene",
   "type": "condition",
   "line_count": 0,
   "condition": "依變數決定：sel_flag"
  },
  {
   "key": "sw:104_01A",
   "tab": "nene",
   "type": "scene",
   "title": "初体験②",
   "chapter": "5",
   "chapter_label": "CHAPTER 5",
   "line_count": 8
  },
  {
   "key": "sw:104_01B",
   "tab": "nene",
   "type": "scene",
   "title": "初体験③",
   "chapter": "5",
   "chapter_label": "CHAPTER 5",
   "line_count": 6
  },
  {
   "key": "sw:104_01com",
   "tab": "nene",
   "type": "merge",
   "title": "初体験（続）",
   "chapter": "5",
   "chapter_label": "CHAPTER 5",
   "line_count": 57
  },
  {
   "key": "sw:105",
   "tab": "nene",
   "type": "choice",
   "title": "報告会",
   "chapter": "6",
   "chapter_label": "CHAPTER 6",
   "line_count": 595
  },
  {
   "key": "sw:105_01A",
   "tab": "nene",
   "type": "option",
   "title": "飲んで欲しい",
   "chapter": "6",
   "chapter_label": "CHAPTER 6",
   "line_count": 24
  },
  {
   "key": "sw:105_01B",
   "tab": "nene",
   "type": "option",
   "title": "顔にかけてみたい",
   "chapter": "6",
   "chapter_label": "CHAPTER 6",
   "line_count": 20
  },
  {
   "key": "sw:105_01com",
   "tab": "nene",
   "type": "merge",
   "title": "報告会（続）",
   "chapter": "6",
   "chapter_label": "CHAPTER 6",
   "line_count": 65
  },
  {
   "key": "sw:106",
   "tab": "nene",
   "type": "scene",
   "title": "急転",
   "chapter": "6",
   "chapter_label": "CHAPTER 6",
   "line_count": 718
  },
  {
   "key": "sw:107",
   "tab": "nene",
   "type": "scene",
   "title": "元鞘",
   "chapter": "6",
   "chapter_label": "CHAPTER 6",
   "line_count": 394
  },
  {
   "key": "sw:108",
   "tab": "nene",
   "type": "choice",
   "title": "決断①",
   "chapter": "7",
   "chapter_label": "CHAPTER 7",
   "line_count": 373
  },
  {
   "key": "sw:108#opt1-0",
   "tab": "nene",
   "type": "option",
   "title": "魔女の中を白く染める",
   "line_count": 0
  },
  {
   "key": "sw:108#opt1-1",
   "tab": "nene",
   "type": "option",
   "title": "魔女の服を白く染める",
   "line_count": 0
  },
  {
   "key": "sw:108_dummy1",
   "tab": "nene",
   "type": "merge",
   "title": "決断（続）",
   "chapter": "7",
   "chapter_label": "CHAPTER 7",
   "line_count": 6
  },
  {
   "key": "sw:108_dummy1#cond",
   "tab": "nene",
   "type": "condition",
   "line_count": 0,
   "condition": "依變數決定：sel_flag"
  },
  {
   "key": "sw:108_01A",
   "tab": "nene",
   "type": "scene",
   "title": "決断②",
   "chapter": "7",
   "chapter_label": "CHAPTER 7",
   "line_count": 8
  },
  {
   "key": "sw:108_01B",
   "tab": "nene",
   "type": "scene",
   "title": "決断③",
   "chapter": "7",
   "chapter_label": "CHAPTER 7",
   "line_count": 5
  },
  {
   "key": "sw:108_01com",
   "tab": "nene",
   "type": "merge",
   "title": "決断（続）",
   "chapter": "7",
   "chapter_label": "CHAPTER 7",
   "line_count": 262
  },
  {
   "key": "sw:109_β",
   "tab": "nene",
   "type": "scene",
   "title": "お別れデート",
   "chapter": "7",
   "chapter_label": "CHAPTER 7",
   "line_count": 538
  },
  {
   "key": "sw:109_β#unlock",
   "tab": "nene",
   "type": "condition",
   "line_count": 0,
   "condition": "寧々通關後 RESTART"
  },
  {
   "key": "sw:110",
   "tab": "nene",
   "type": "scene",
   "title": "restart（寧々編）",
   "chapter": "0",
   "chapter_label": "RESTART · CHAPTER 0",
   "line_count": 355
  },
  {
   "key": "sw:111",
   "tab": "nene",
   "type": "scene",
   "title": "restart（本編）",
   "chapter": "1",
   "chapter_label": "RESTART · CHAPTER 1",
   "line_count": 911
  },
  {
   "key": "sw:112",
   "tab": "nene",
   "type": "scene",
   "title": "restart（２週目Ｈ）①",
   "chapter": "2",
   "chapter_label": "RESTART · CHAPTER 2",
   "line_count": 8
  },
  {
   "key": "sw:112_01B",
   "tab": "nene",
   "type": "scene",
   "title": "restart（２週目Ｈ）②",
   "chapter": "2",
   "chapter_label": "RESTART · CHAPTER 2",
   "line_count": 7
  },
  {
   "key": "sw:112_01com",
   "tab": "nene",
   "type": "merge",
   "title": "restart（２週目Ｈ）（続）",
   "chapter": "2",
   "chapter_label": "RESTART · CHAPTER 2",
   "line_count": 35
  },
  {
   "key": "sw:112_chapter21",
   "tab": "nene",
   "type": "choice",
   "title": "restart（２週目Ｈ）③",
   "chapter": "2",
   "chapter_label": "RESTART · CHAPTER 2",
   "line_count": 1204
  },
  {
   "key": "sw:112#opt1-0",
   "tab": "nene",
   "type": "option",
   "title": "このまま中に",
   "line_count": 0
  },
  {
   "key": "sw:112#opt1-1",
   "tab": "nene",
   "type": "option",
   "title": "このまま外に",
   "line_count": 0
  },
  {
   "key": "sw:112_dummy1",
   "tab": "nene",
   "type": "merge",
   "title": "restart（２週目Ｈ）（続）",
   "chapter": "2",
   "chapter_label": "RESTART · CHAPTER 2",
   "line_count": 6
  },
  {
   "key": "sw:112_dummy1#cond",
   "tab": "nene",
   "type": "condition",
   "line_count": 0,
   "condition": "依變數決定：sel_flag"
  },
  {
   "key": "sw:113",
   "tab": "nene",
   "type": "choice",
   "title": "restart（再び部活）①",
   "chapter": "2",
   "chapter_label": "RESTART · CHAPTER 2",
   "line_count": 997
  },
  {
   "key": "sw:113_01A",
   "tab": "nene",
   "type": "option",
   "title": "わかった",
   "chapter": "2",
   "chapter_label": "RESTART · CHAPTER 2",
   "line_count": 2
  },
  {
   "key": "sw:113_01B",
   "tab": "nene",
   "type": "option",
   "title": "オレはそんなこと気にしない",
   "chapter": "2",
   "chapter_label": "RESTART · CHAPTER 2",
   "line_count": 8
  },
  {
   "key": "sw:113_01com",
   "tab": "nene",
   "type": "merge",
   "title": "restart（再び部活）（続）",
   "chapter": "2",
   "chapter_label": "RESTART · CHAPTER 2",
   "line_count": 3
  },
  {
   "key": "sw:113_01com#cond",
   "tab": "nene",
   "type": "condition",
   "line_count": 0,
   "condition": "依條件決定"
  },
  {
   "key": "sw:113_01A_eye",
   "tab": "nene",
   "type": "scene",
   "title": "restart（再び部活）②",
   "chapter": "2",
   "chapter_label": "RESTART · CHAPTER 2",
   "line_count": 2
  },
  {
   "key": "sw:113_01B_eye",
   "tab": "nene",
   "type": "scene",
   "title": "restart（再び部活）③",
   "chapter": "2",
   "chapter_label": "RESTART · CHAPTER 2",
   "line_count": 3
  },
  {
   "key": "sw:113_01com_eye",
   "tab": "nene",
   "type": "choice",
   "title": "restart（再び部活）④",
   "chapter": "2",
   "chapter_label": "RESTART · CHAPTER 2",
   "line_count": 124
  },
  {
   "key": "sw:113#opt2-0",
   "tab": "nene",
   "type": "option",
   "title": "このまま中で一気に",
   "line_count": 0
  },
  {
   "key": "sw:113#opt2-1",
   "tab": "nene",
   "type": "option",
   "title": "寧々の身体にぶちまける",
   "line_count": 0
  },
  {
   "key": "sw:113_dummy2",
   "tab": "nene",
   "type": "merge",
   "title": "restart（再び部活）（続）",
   "chapter": "2",
   "chapter_label": "RESTART · CHAPTER 2",
   "line_count": 7
  },
  {
   "key": "sw:113_dummy2#cond",
   "tab": "nene",
   "type": "condition",
   "line_count": 0,
   "condition": "依變數決定：eye_flag, sel_flag"
  },
  {
   "key": "sw:113_02A",
   "tab": "nene",
   "type": "scene",
   "title": "restart（再び部活）⑤",
   "chapter": "2",
   "chapter_label": "RESTART · CHAPTER 2",
   "line_count": 14
  },
  {
   "key": "sw:113_02com",
   "tab": "nene",
   "type": "merge",
   "title": "restart（再び部活）（続）",
   "chapter": "2",
   "chapter_label": "RESTART · CHAPTER 2",
   "line_count": 12
  },
  {
   "key": "sw:113_02com#cond",
   "tab": "nene",
   "type": "condition",
   "line_count": 0,
   "condition": "依條件決定"
  },
  {
   "key": "sw:113_02A_eye",
   "tab": "nene",
   "type": "scene",
   "title": "restart（再び部活）⑥",
   "chapter": "2",
   "chapter_label": "RESTART · CHAPTER 2",
   "line_count": 6
  },
  {
   "key": "sw:113_02B",
   "tab": "nene",
   "type": "scene",
   "title": "restart（再び部活）⑦",
   "chapter": "2",
   "chapter_label": "RESTART · CHAPTER 2",
   "line_count": 6
  },
  {
   "key": "sw:113_02B_eye",
   "tab": "nene",
   "type": "scene",
   "title": "restart（再び部活）⑧",
   "chapter": "2",
   "chapter_label": "RESTART · CHAPTER 2",
   "line_count": 3
  },
  {
   "key": "sw:113_02com_eye",
   "tab": "nene",
   "type": "merge",
   "title": "restart（再び部活）（続）",
   "chapter": "2",
   "chapter_label": "RESTART · CHAPTER 2",
   "line_count": 19
  },
  {
   "key": "sw:114",
   "tab": "nene",
   "type": "scene",
   "title": "restart（変わりゆく日々）①",
   "chapter": "2",
   "chapter_label": "RESTART · CHAPTER 2",
   "line_count": 59
  },
  {
   "key": "sw:114_chapter31",
   "tab": "nene",
   "type": "scene",
   "title": "restart（変わりゆく日々）②",
   "chapter": "3",
   "chapter_label": "RESTART · CHAPTER 3",
   "line_count": 585
  },
  {
   "key": "sw:115",
   "tab": "nene",
   "type": "scene",
   "title": "restart（二度目の本番）①",
   "chapter": "3",
   "chapter_label": "RESTART · CHAPTER 3",
   "line_count": 9
  },
  {
   "key": "sw:115_01B",
   "tab": "nene",
   "type": "scene",
   "title": "restart（二度目の本番）②",
   "chapter": "3",
   "chapter_label": "RESTART · CHAPTER 3",
   "line_count": 8
  },
  {
   "key": "sw:115_01com",
   "tab": "nene",
   "type": "choice",
   "title": "restart（二度目の本番）③",
   "chapter": "3",
   "chapter_label": "RESTART · CHAPTER 3",
   "line_count": 275
  },
  {
   "key": "sw:115_02A",
   "tab": "nene",
   "type": "option",
   "title": "このままイく",
   "chapter": "3",
   "chapter_label": "RESTART · CHAPTER 3",
   "line_count": 15
  },
  {
   "key": "sw:115_02B",
   "tab": "nene",
   "type": "option",
   "title": "最後に自分で動いてイく",
   "chapter": "3",
   "chapter_label": "RESTART · CHAPTER 3",
   "line_count": 13
  },
  {
   "key": "sw:115_02com",
   "tab": "nene",
   "type": "merge",
   "title": "restart（二度目の本番）（続）",
   "chapter": "3",
   "chapter_label": "RESTART · CHAPTER 3",
   "line_count": 154
  },
  {
   "key": "sw:115_chapter35",
   "tab": "nene",
   "type": "choice",
   "title": "restart（二度目の本番）④",
   "chapter": "3",
   "chapter_label": "RESTART · CHAPTER 3",
   "line_count": 446
  },
  {
   "key": "sw:115#opt1-0",
   "tab": "nene",
   "type": "option",
   "title": "このまま中で一気に",
   "line_count": 0
  },
  {
   "key": "sw:115#opt1-1",
   "tab": "nene",
   "type": "option",
   "title": "寧々の身体にぶちまける",
   "line_count": 0
  },
  {
   "key": "sw:115_dummy1",
   "tab": "nene",
   "type": "merge",
   "title": "restart（二度目の本番）（続）",
   "chapter": "3",
   "chapter_label": "RESTART · CHAPTER 3",
   "line_count": 6
  },
  {
   "key": "sw:115_dummy1#cond",
   "tab": "nene",
   "type": "condition",
   "line_count": 0,
   "condition": "依變數決定：sel_flag"
  },
  {
   "key": "sw:116",
   "tab": "nene",
   "type": "scene",
   "title": "エピローグ",
   "chapter": "epilogue",
   "chapter_label": "RESTART · Epilogue",
   "line_count": 210
  },
  {
   "key": "sw:500",
   "tab": "wakana",
   "type": "scene",
   "title": "分岐",
   "chapter": "4",
   "chapter_label": "CHAPTER 4",
   "line_count": 227
  },
  {
   "key": "sw:501",
   "tab": "wakana",
   "type": "scene",
   "title": "ハプニング",
   "chapter": "4",
   "chapter_label": "CHAPTER 4",
   "line_count": 242
  },
  {
   "key": "sw:502",
   "tab": "wakana",
   "type": "scene",
   "title": "意識する日々",
   "chapter": "5",
   "chapter_label": "CHAPTER 5",
   "line_count": 381
  },
  {
   "key": "sw:503",
   "tab": "wakana",
   "type": "scene",
   "title": "セカンドキス",
   "chapter": "5",
   "chapter_label": "CHAPTER 5",
   "line_count": 463
  },
  {
   "key": "sw:504",
   "tab": "wakana",
   "type": "scene",
   "title": "ドギマギな日々",
   "chapter": "5",
   "chapter_label": "CHAPTER 5",
   "line_count": 426
  },
  {
   "key": "sw:505",
   "tab": "wakana",
   "type": "scene",
   "title": "ライブ本番",
   "chapter": "6",
   "chapter_label": "CHAPTER 6",
   "line_count": 437
  },
  {
   "key": "sw:506",
   "tab": "wakana",
   "type": "choice",
   "title": "レロレロ①",
   "chapter": "6",
   "chapter_label": "CHAPTER 6",
   "line_count": 349
  },
  {
   "key": "sw:506_01A",
   "tab": "wakana",
   "type": "option",
   "title": "口で受け止めて欲しい",
   "chapter": "6",
   "chapter_label": "CHAPTER 6",
   "line_count": 19
  },
  {
   "key": "sw:506_01com",
   "tab": "wakana",
   "type": "choice",
   "title": "レロレロ②",
   "chapter": "6",
   "chapter_label": "CHAPTER 6",
   "line_count": 255
  },
  {
   "key": "sw:506_02A",
   "tab": "wakana",
   "type": "option",
   "title": "腰をずらして勝負",
   "chapter": "6",
   "chapter_label": "CHAPTER 6",
   "line_count": 17
  },
  {
   "key": "sw:506_02com",
   "tab": "wakana",
   "type": "choice",
   "title": "レロレロ③",
   "chapter": "6",
   "chapter_label": "CHAPTER 6",
   "line_count": 455
  },
  {
   "key": "sw:506#opt3-0",
   "tab": "wakana",
   "type": "option",
   "title": "和奏の中に全てを解き放つ！",
   "line_count": 0
  },
  {
   "key": "sw:506#opt3-1",
   "tab": "wakana",
   "type": "option",
   "title": "和奏の身体にかけたい！",
   "line_count": 0
  },
  {
   "key": "sw:506_02B",
   "tab": "wakana",
   "type": "option",
   "title": "このまま真っ向勝負",
   "chapter": "6",
   "chapter_label": "CHAPTER 6",
   "line_count": 29
  },
  {
   "key": "sw:506_01B",
   "tab": "wakana",
   "type": "option",
   "title": "顔で受け止めて欲しい",
   "chapter": "6",
   "chapter_label": "CHAPTER 6",
   "line_count": 13
  },
  {
   "key": "sw:506_dummy1",
   "tab": "wakana",
   "type": "merge",
   "title": "レロレロ（続）",
   "chapter": "6",
   "chapter_label": "CHAPTER 6",
   "line_count": 7
  },
  {
   "key": "sw:506_dummy1#cond",
   "tab": "wakana",
   "type": "condition",
   "line_count": 0,
   "condition": "依變數決定：sel_flag"
  },
  {
   "key": "sw:506_03A",
   "tab": "wakana",
   "type": "scene",
   "title": "レロレロ④",
   "chapter": "6",
   "chapter_label": "CHAPTER 6",
   "line_count": 13
  },
  {
   "key": "sw:506_03B",
   "tab": "wakana",
   "type": "scene",
   "title": "レロレロ⑤",
   "chapter": "6",
   "chapter_label": "CHAPTER 6",
   "line_count": 12
  },
  {
   "key": "sw:506_03com",
   "tab": "wakana",
   "type": "merge",
   "title": "レロレロ（続）",
   "chapter": "6",
   "chapter_label": "CHAPTER 6",
   "line_count": 171
  },
  {
   "key": "sw:507",
   "tab": "wakana",
   "type": "scene",
   "title": "エピローグ的な",
   "chapter": "epilogue",
   "chapter_label": "Epilogue",
   "line_count": 75
  },
  {
   "key": "sw:117",
   "tab": "nene",
   "type": "choice",
   "title": "アフター①",
   "chapter": "after",
   "chapter_label": "After Story",
   "line_count": 277
  },
  {
   "key": "sw:117_01A",
   "tab": "nene",
   "type": "option",
   "title": "いや、我慢できるはず！",
   "chapter": "after",
   "chapter_label": "After Story",
   "line_count": 25
  },
  {
   "key": "sw:117_01B",
   "tab": "nene",
   "type": "option",
   "title": "やっぱりもう無理だ！",
   "chapter": "after",
   "chapter_label": "After Story",
   "line_count": 23
  },
  {
   "key": "sw:117_01com",
   "tab": "nene",
   "type": "merge",
   "title": "アフター（続）",
   "chapter": "after",
   "chapter_label": "After Story",
   "line_count": 49
  },
  {
   "key": "sw:117_01com#cond",
   "tab": "nene",
   "type": "condition",
   "line_count": 0,
   "condition": "依條件決定"
  },
  {
   "key": "sw:117_02A",
   "tab": "nene",
   "type": "scene",
   "title": "アフター②",
   "chapter": "after",
   "chapter_label": "After Story",
   "line_count": 5
  },
  {
   "key": "sw:117_02B",
   "tab": "nene",
   "type": "scene",
   "title": "アフター③",
   "chapter": "after",
   "chapter_label": "After Story",
   "line_count": 5
  },
  {
   "key": "sw:117_02com",
   "tab": "nene",
   "type": "choice",
   "title": "アフター④",
   "chapter": "after",
   "chapter_label": "After Story",
   "line_count": 155
  },
  {
   "key": "sw:117#opt2-0",
   "tab": "nene",
   "type": "option",
   "title": "このまま出す！",
   "line_count": 0
  },
  {
   "key": "sw:117#opt2-1",
   "tab": "nene",
   "type": "option",
   "title": "少し慎重になる",
   "line_count": 0
  },
  {
   "key": "sw:117_dummy1",
   "tab": "nene",
   "type": "merge",
   "title": "アフター（続）",
   "chapter": "after",
   "chapter_label": "After Story",
   "line_count": 15
  },
  {
   "key": "sw:117_dummy1#cond",
   "tab": "nene",
   "type": "condition",
   "line_count": 0,
   "condition": "依變數決定：sel_flag"
  },
  {
   "key": "sw:117_03A",
   "tab": "nene",
   "type": "scene",
   "title": "アフター⑤",
   "chapter": "after",
   "chapter_label": "After Story",
   "line_count": 4
  },
  {
   "key": "sw:117_03B",
   "tab": "nene",
   "type": "scene",
   "title": "アフター⑥",
   "chapter": "after",
   "chapter_label": "After Story",
   "line_count": 5
  },
  {
   "key": "sw:117_03com",
   "tab": "nene",
   "type": "merge",
   "title": "アフター（続）",
   "chapter": "after",
   "chapter_label": "After Story",
   "line_count": 51
  },
  {
   "key": "sw:508",
   "tab": "wakana",
   "type": "choice",
   "title": "アフター①",
   "chapter": "after",
   "chapter_label": "After Story",
   "line_count": 299
  },
  {
   "key": "sw:508_01A",
   "tab": "wakana",
   "type": "option",
   "title": "素直に従う",
   "chapter": "after",
   "chapter_label": "After Story",
   "line_count": 12
  },
  {
   "key": "sw:508_01B",
   "tab": "wakana",
   "type": "option",
   "title": "少しは抵抗する",
   "chapter": "after",
   "chapter_label": "After Story",
   "line_count": 12
  },
  {
   "key": "sw:508_01com",
   "tab": "wakana",
   "type": "choice",
   "title": "アフター②",
   "chapter": "after",
   "chapter_label": "After Story",
   "line_count": 130
  },
  {
   "key": "sw:508_02A",
   "tab": "wakana",
   "type": "option",
   "title": "舐められる方",
   "chapter": "after",
   "chapter_label": "After Story",
   "line_count": 9
  },
  {
   "key": "sw:508_02B",
   "tab": "wakana",
   "type": "option",
   "title": "しゃぶられる方",
   "chapter": "after",
   "chapter_label": "After Story",
   "line_count": 9
  },
  {
   "key": "sw:508_02com",
   "tab": "wakana",
   "type": "merge",
   "title": "アフター（続）",
   "chapter": "after",
   "chapter_label": "After Story",
   "line_count": 65
  }
 ],
 "edges": [
  {
   "from": "sw:001",
   "to": "sw:002",
   "kind": "normal"
  },
  {
   "from": "sw:002",
   "to": "sw:003",
   "kind": "normal"
  },
  {
   "from": "sw:003",
   "to": "sw:004",
   "kind": "normal"
  },
  {
   "from": "sw:004",
   "to": "sw:005",
   "kind": "normal"
  },
  {
   "from": "sw:005",
   "to": "sw:005_01A",
   "kind": "branch",
   "order": 0,
   "label": "もう１回正直に褒める",
   "effects": [
    {
     "var": "sel_flag",
     "op": "set",
     "value": 0
    },
    {
     "var": "wak_flag",
     "op": "add",
     "value": 1
    }
   ]
  },
  {
   "from": "sw:005",
   "to": "sw:005#opt1-1",
   "kind": "branch",
   "order": 1,
   "label": "しつこいからもういいか",
   "effects": [
    {
     "var": "sel_flag",
     "op": "set",
     "value": 1
    }
   ]
  },
  {
   "from": "sw:005#opt1-1",
   "to": "sw:005_01com",
   "kind": "merge"
  },
  {
   "from": "sw:005_01A",
   "to": "sw:005_01com",
   "kind": "merge"
  },
  {
   "from": "sw:005_01com",
   "to": "sw:006",
   "kind": "normal"
  },
  {
   "from": "sw:006",
   "to": "sw:006_01A",
   "kind": "branch",
   "order": 0,
   "label": "綾地さんに頼む",
   "effects": [
    {
     "var": "sel_flag",
     "op": "set",
     "value": 0
    },
    {
     "var": "nen_flag",
     "op": "add",
     "value": 1
    }
   ]
  },
  {
   "from": "sw:006",
   "to": "sw:006_01B",
   "kind": "branch",
   "order": 1,
   "label": "このまま因幡さんと続ける",
   "effects": [
    {
     "var": "sel_flag",
     "op": "set",
     "value": 1
    },
    {
     "var": "meg_flag",
     "op": "add",
     "value": 1
    }
   ]
  },
  {
   "from": "sw:006_01A",
   "to": "sw:006_01com",
   "kind": "merge"
  },
  {
   "from": "sw:006_01com",
   "to": "sw:007",
   "kind": "normal"
  },
  {
   "from": "sw:006_01B",
   "to": "sw:006_01com",
   "kind": "merge"
  },
  {
   "from": "sw:007",
   "to": "sw:008",
   "kind": "normal"
  },
  {
   "from": "sw:008",
   "to": "sw:009",
   "kind": "normal"
  },
  {
   "from": "sw:009",
   "to": "sw:009#opt1-0",
   "kind": "branch",
   "order": 0,
   "label": "……悩む",
   "effects": [
    {
     "var": "sel_flag",
     "op": "set",
     "value": 0
    }
   ]
  },
  {
   "from": "sw:009",
   "to": "sw:009#opt1-1",
   "kind": "branch",
   "order": 1,
   "label": "時間を変更する",
   "effects": [
    {
     "var": "sel_flag",
     "op": "set",
     "value": 1
    }
   ]
  },
  {
   "from": "sw:009#opt1-0",
   "to": "sw:009_01A",
   "kind": "normal"
  },
  {
   "from": "sw:009#opt1-1",
   "to": "sw:009_01B",
   "kind": "normal"
  },
  {
   "from": "sw:009_01A",
   "to": "sw:009_02A",
   "kind": "branch",
   "order": 0,
   "label": "いや、普通に映画に集中",
   "effects": [
    {
     "var": "sel_flag",
     "op": "set",
     "value": 0
    }
   ]
  },
  {
   "from": "sw:009_01A",
   "to": "sw:009_02B",
   "kind": "branch",
   "order": 1,
   "label": "……これもリハーサル",
   "effects": [
    {
     "var": "sel_flag",
     "op": "set",
     "value": 1
    },
    {
     "var": "meg_flag",
     "op": "add",
     "value": 1
    }
   ]
  },
  {
   "from": "sw:009_02A",
   "to": "sw:009_01com",
   "kind": "normal"
  },
  {
   "from": "sw:009_01com",
   "to": "sw:009_04A",
   "kind": "branch",
   "order": 0,
   "label": "かもね",
   "effects": [
    {
     "var": "sel_flag",
     "op": "set",
     "value": 0
    }
   ]
  },
  {
   "from": "sw:009_01com",
   "to": "sw:009_04B",
   "kind": "branch",
   "order": 1,
   "label": "別に",
   "effects": [
    {
     "var": "sel_flag",
     "op": "set",
     "value": 1
    },
    {
     "var": "meg_flag",
     "op": "add",
     "value": 1
    }
   ]
  },
  {
   "from": "sw:009_04A",
   "to": "sw:009_04com",
   "kind": "merge"
  },
  {
   "from": "sw:009_04com",
   "to": "sw:010",
   "kind": "normal"
  },
  {
   "from": "sw:009_04B",
   "to": "sw:009_04com",
   "kind": "merge"
  },
  {
   "from": "sw:009_02B",
   "to": "sw:009_01com",
   "kind": "normal"
  },
  {
   "from": "sw:009_01B",
   "to": "sw:009_03A",
   "kind": "branch",
   "order": 0,
   "label": "このままみんなで移動",
   "effects": [
    {
     "var": "sel_flag",
     "op": "set",
     "value": 0
    }
   ]
  },
  {
   "from": "sw:009_01B",
   "to": "sw:009_03B",
   "kind": "branch",
   "order": 1,
   "label": "空腹を解消したい",
   "effects": [
    {
     "var": "sel_flag",
     "op": "set",
     "value": 1
    }
   ]
  },
  {
   "from": "sw:009_03A",
   "to": "sw:009_01com",
   "kind": "normal"
  },
  {
   "from": "sw:009_03B",
   "to": "sw:009_03B#cond",
   "kind": "normal"
  },
  {
   "from": "sw:009_03B#cond",
   "to": "sw:009_wak_com",
   "kind": "branch",
   "order": 0,
   "label": "その他"
  },
  {
   "from": "sw:009_03B#cond",
   "to": "sw:009_dummy1",
   "kind": "branch",
   "order": 1,
   "label": "例：wak_flag=1"
  },
  {
   "from": "sw:009_wak_com",
   "to": "sw:009_01com",
   "kind": "normal"
  },
  {
   "from": "sw:009_dummy1",
   "to": "sw:009_wak_com",
   "kind": "merge"
  },
  {
   "from": "sw:010",
   "to": "sw:010_01A",
   "kind": "branch",
   "order": 0,
   "label": "ちょっとじゃれてみる",
   "effects": [
    {
     "var": "sel_flag",
     "op": "set",
     "value": 0
    },
    {
     "var": "wak_flag",
     "op": "add",
     "value": 1
    }
   ]
  },
  {
   "from": "sw:010",
   "to": "sw:010_01B",
   "kind": "branch",
   "order": 1,
   "label": "普通に言う",
   "effects": [
    {
     "var": "sel_flag",
     "op": "set",
     "value": 1
    }
   ]
  },
  {
   "from": "sw:010_01A",
   "to": "sw:010_01com",
   "kind": "merge"
  },
  {
   "from": "sw:010_01com",
   "to": "sw:011",
   "kind": "normal"
  },
  {
   "from": "sw:010_01B",
   "to": "sw:010_01com",
   "kind": "merge"
  },
  {
   "from": "sw:011",
   "to": "sw:012",
   "kind": "normal"
  },
  {
   "from": "sw:012",
   "to": "sw:013",
   "kind": "normal"
  },
  {
   "from": "sw:013",
   "to": "sw:014",
   "kind": "normal"
  },
  {
   "from": "sw:014",
   "to": "sw:014_01A",
   "kind": "branch",
   "order": 0,
   "label": "誤魔化す",
   "effects": [
    {
     "var": "sel_flag",
     "op": "set",
     "value": 0
    }
   ]
  },
  {
   "from": "sw:014",
   "to": "sw:014_01B",
   "kind": "branch",
   "order": 1,
   "label": "正直に言う",
   "effects": [
    {
     "var": "sel_flag",
     "op": "set",
     "value": 1
    },
    {
     "var": "nen_flag",
     "op": "add",
     "value": 1
    }
   ]
  },
  {
   "from": "sw:014_01A",
   "to": "sw:014_01com",
   "kind": "merge"
  },
  {
   "from": "sw:014_01com",
   "to": "sw:015",
   "kind": "normal"
  },
  {
   "from": "sw:014_01B",
   "to": "sw:014_01com",
   "kind": "merge"
  },
  {
   "from": "sw:015",
   "to": "sw:015#cond",
   "kind": "normal"
  },
  {
   "from": "sw:015#cond",
   "to": "sw:015_01A",
   "kind": "branch",
   "order": 0,
   "label": "その他"
  },
  {
   "from": "sw:015#cond",
   "to": "sw:015_01B",
   "kind": "branch",
   "order": 1,
   "label": "例：nen_flag=2"
  },
  {
   "from": "sw:015_01A",
   "to": "sw:015_01com",
   "kind": "merge"
  },
  {
   "from": "sw:015_01B",
   "to": "sw:015_01com",
   "kind": "merge"
  },
  {
   "from": "sw:015_01com",
   "to": "sw:016",
   "kind": "normal"
  },
  {
   "from": "sw:016",
   "to": "sw:017",
   "kind": "normal"
  },
  {
   "from": "sw:017",
   "to": "sw:017_01A",
   "kind": "branch",
   "order": 0,
   "label": "諦めて気分を変える",
   "effects": [
    {
     "var": "sel_flag",
     "op": "set",
     "value": 0
    },
    {
     "var": "tsu_flag",
     "op": "add",
     "value": 1
    }
   ]
  },
  {
   "from": "sw:017",
   "to": "sw:017_01B",
   "kind": "branch",
   "order": 1,
   "label": "いい機会だから教えてもらう",
   "effects": [
    {
     "var": "sel_flag",
     "op": "set",
     "value": 1
    },
    {
     "var": "tou_flag",
     "op": "add",
     "value": 1
    }
   ]
  },
  {
   "from": "sw:017_01A",
   "to": "sw:017_01com",
   "kind": "merge"
  },
  {
   "from": "sw:017_01com",
   "to": "sw:018",
   "kind": "normal"
  },
  {
   "from": "sw:017_01B",
   "to": "sw:017_01com",
   "kind": "merge"
  },
  {
   "from": "sw:018",
   "to": "sw:018_01A",
   "kind": "branch",
   "order": 0,
   "label": "もう一度握ってもらう",
   "effects": [
    {
     "var": "sel_flag",
     "op": "set",
     "value": 0
    },
    {
     "var": "nen_flag",
     "op": "add",
     "value": 1
    }
   ]
  },
  {
   "from": "sw:018",
   "to": "sw:018_01B",
   "kind": "branch",
   "order": 1,
   "label": "さすがにそんなことは言えない",
   "effects": [
    {
     "var": "sel_flag",
     "op": "set",
     "value": 1
    }
   ]
  },
  {
   "from": "sw:018_01A",
   "to": "sw:018_01com",
   "kind": "merge"
  },
  {
   "from": "sw:018_01com",
   "to": "sw:018_01com#unlock",
   "kind": "normal"
  },
  {
   "from": "sw:018_01com#unlock",
   "to": "sw:019",
   "kind": "branch",
   "order": 0,
   "label": "その他"
  },
  {
   "from": "sw:018_01com#unlock",
   "to": "sw:route:wakana",
   "kind": "branch",
   "order": 1,
   "label": "任一路線通關後",
   "flags": [
    "unlock",
    "inferred"
   ]
  },
  {
   "from": "sw:018_01B",
   "to": "sw:018_01com",
   "kind": "merge"
  },
  {
   "from": "sw:019",
   "to": "sw:019_01A",
   "kind": "branch",
   "order": 0,
   "label": "褒める",
   "effects": [
    {
     "var": "sel_flag",
     "op": "set",
     "value": 0
    },
    {
     "var": "tou_flag",
     "op": "add",
     "value": 1
    }
   ]
  },
  {
   "from": "sw:019",
   "to": "sw:019_01B",
   "kind": "branch",
   "order": 1,
   "label": "目をそらす",
   "effects": [
    {
     "var": "sel_flag",
     "op": "set",
     "value": 1
    }
   ]
  },
  {
   "from": "sw:019_01A",
   "to": "sw:019",
   "kind": "back"
  },
  {
   "from": "sw:019_01A",
   "to": "sw:019_01com",
   "kind": "normal"
  },
  {
   "from": "sw:019_01B",
   "to": "sw:019_01com",
   "kind": "normal"
  },
  {
   "from": "sw:019_01com",
   "to": "sw:019_02A",
   "kind": "branch",
   "order": 0,
   "label": "因幡さん、かな",
   "effects": [
    {
     "var": "sel_flag",
     "op": "set",
     "value": 0
    },
    {
     "var": "meg_flag",
     "op": "add",
     "value": 1
    }
   ]
  },
  {
   "from": "sw:019_01com",
   "to": "sw:019_02B",
   "kind": "branch",
   "order": 1,
   "label": "綾地さんがいいかな",
   "effects": [
    {
     "var": "sel_flag",
     "op": "set",
     "value": 1
    },
    {
     "var": "nen_flag",
     "op": "add",
     "value": 1
    }
   ]
  },
  {
   "from": "sw:019_01com",
   "to": "sw:019_02C",
   "kind": "branch",
   "order": 2,
   "label": "やっぱり……戸隠先輩？",
   "effects": [
    {
     "var": "sel_flag",
     "op": "set",
     "value": 2
    },
    {
     "var": "tou_flag",
     "op": "add",
     "value": 1
    }
   ]
  },
  {
   "from": "sw:019_01com",
   "to": "sw:019_02D",
   "kind": "branch",
   "order": 3,
   "label": "今はバンドの事で頭がいっぱい",
   "effects": [
    {
     "var": "sel_flag",
     "op": "set",
     "value": 3
    },
    {
     "var": "tsu_flag",
     "op": "add",
     "value": 1
    }
   ]
  },
  {
   "from": "sw:019_02A",
   "to": "sw:019_02com",
   "kind": "merge"
  },
  {
   "from": "sw:019_02com",
   "to": "sw:019_02com#cond",
   "kind": "normal"
  },
  {
   "from": "sw:019_02com#cond",
   "to": "sw:019_normal_end",
   "kind": "branch",
   "order": 0,
   "label": "好感旗標不足"
  },
  {
   "from": "sw:019_02com#cond",
   "to": "sw:020.共通－パーティーめぐる",
   "kind": "branch",
   "order": 1,
   "label": "例：meg_flag=4"
  },
  {
   "from": "sw:019_02com#cond",
   "to": "sw:020.共通－パーティ寧々",
   "kind": "branch",
   "order": 2,
   "label": "例：nen_flag=4"
  },
  {
   "from": "sw:019_02com#cond",
   "to": "sw:020.共通－パーティ憧子",
   "kind": "branch",
   "order": 3,
   "label": "例：tou_flag=3"
  },
  {
   "from": "sw:019_02com#cond",
   "to": "sw:020.共通－パーティー紬",
   "kind": "branch",
   "order": 4,
   "label": "例：tsu_flag=2"
  },
  {
   "from": "sw:019_02B",
   "to": "sw:019_02com",
   "kind": "merge"
  },
  {
   "from": "sw:019_02C",
   "to": "sw:019_02com",
   "kind": "merge"
  },
  {
   "from": "sw:019_02D",
   "to": "sw:019_02com",
   "kind": "merge"
  },
  {
   "from": "sw:020.共通－パーティーめぐる",
   "to": "sw:route:meguru",
   "kind": "normal"
  },
  {
   "from": "sw:020.共通－パーティー紬",
   "to": "sw:route:tsumugi",
   "kind": "normal"
  },
  {
   "from": "sw:020.共通－パーティ寧々",
   "to": "sw:route:nene",
   "kind": "normal"
  },
  {
   "from": "sw:020.共通－パーティ憧子",
   "to": "sw:route:touko",
   "kind": "normal"
  },
  {
   "from": "sw:101",
   "to": "sw:102",
   "kind": "normal"
  },
  {
   "from": "sw:102",
   "to": "sw:103",
   "kind": "normal"
  },
  {
   "from": "sw:103",
   "to": "sw:104",
   "kind": "normal"
  },
  {
   "from": "sw:104",
   "to": "sw:104#opt1-0",
   "kind": "branch",
   "order": 0,
   "label": "このまま射精したい",
   "effects": [
    {
     "var": "sel_flag",
     "op": "set",
     "value": 0
    }
   ]
  },
  {
   "from": "sw:104",
   "to": "sw:104#opt1-1",
   "kind": "branch",
   "order": 1,
   "label": "身体を汚したい",
   "effects": [
    {
     "var": "sel_flag",
     "op": "set",
     "value": 1
    }
   ]
  },
  {
   "from": "sw:104#opt1-0",
   "to": "sw:104_dummy1",
   "kind": "merge"
  },
  {
   "from": "sw:104#opt1-1",
   "to": "sw:104_dummy1",
   "kind": "merge"
  },
  {
   "from": "sw:104_dummy1",
   "to": "sw:104_dummy1#cond",
   "kind": "normal"
  },
  {
   "from": "sw:104_dummy1#cond",
   "to": "sw:104_01A",
   "kind": "branch",
   "order": 0,
   "label": "例：sel_flag=0"
  },
  {
   "from": "sw:104_dummy1#cond",
   "to": "sw:104_01B",
   "kind": "branch",
   "order": 1,
   "label": "例：sel_flag=1"
  },
  {
   "from": "sw:104_01A",
   "to": "sw:104_01com",
   "kind": "merge"
  },
  {
   "from": "sw:104_01B",
   "to": "sw:104_01com",
   "kind": "merge"
  },
  {
   "from": "sw:104_01com",
   "to": "sw:105",
   "kind": "normal"
  },
  {
   "from": "sw:105",
   "to": "sw:105_01A",
   "kind": "branch",
   "order": 0,
   "label": "飲んで欲しい",
   "effects": [
    {
     "var": "sel_flag",
     "op": "set",
     "value": 0
    }
   ]
  },
  {
   "from": "sw:105",
   "to": "sw:105_01B",
   "kind": "branch",
   "order": 1,
   "label": "顔にかけてみたい",
   "effects": [
    {
     "var": "sel_flag",
     "op": "set",
     "value": 1
    }
   ]
  },
  {
   "from": "sw:105_01A",
   "to": "sw:105",
   "kind": "back"
  },
  {
   "from": "sw:105_01A",
   "to": "sw:105_01com",
   "kind": "merge"
  },
  {
   "from": "sw:105_01B",
   "to": "sw:105_01com",
   "kind": "merge"
  },
  {
   "from": "sw:105_01com",
   "to": "sw:106",
   "kind": "normal"
  },
  {
   "from": "sw:106",
   "to": "sw:107",
   "kind": "normal"
  },
  {
   "from": "sw:107",
   "to": "sw:108",
   "kind": "normal"
  },
  {
   "from": "sw:108",
   "to": "sw:108#opt1-0",
   "kind": "branch",
   "order": 0,
   "label": "魔女の中を白く染める",
   "effects": [
    {
     "var": "sel_flag",
     "op": "set",
     "value": 0
    }
   ]
  },
  {
   "from": "sw:108",
   "to": "sw:108#opt1-1",
   "kind": "branch",
   "order": 1,
   "label": "魔女の服を白く染める",
   "effects": [
    {
     "var": "sel_flag",
     "op": "set",
     "value": 1
    }
   ]
  },
  {
   "from": "sw:108#opt1-0",
   "to": "sw:108_dummy1",
   "kind": "merge"
  },
  {
   "from": "sw:108#opt1-1",
   "to": "sw:108_dummy1",
   "kind": "merge"
  },
  {
   "from": "sw:108_dummy1",
   "to": "sw:108_dummy1#cond",
   "kind": "normal"
  },
  {
   "from": "sw:108_dummy1#cond",
   "to": "sw:108_01A",
   "kind": "branch",
   "order": 0,
   "label": "例：sel_flag=0"
  },
  {
   "from": "sw:108_dummy1#cond",
   "to": "sw:108_01B",
   "kind": "branch",
   "order": 1,
   "label": "例：sel_flag=1"
  },
  {
   "from": "sw:108_01A",
   "to": "sw:108_01com",
   "kind": "merge"
  },
  {
   "from": "sw:108_01B",
   "to": "sw:108_01com",
   "kind": "merge"
  },
  {
   "from": "sw:108_01com",
   "to": "sw:109_β",
   "kind": "normal"
  },
  {
   "from": "sw:109_β",
   "to": "sw:109_β#unlock",
   "kind": "normal"
  },
  {
   "from": "sw:109_β#unlock",
   "to": "sw:110",
   "kind": "branch",
   "order": 0,
   "label": "寧々通關後 RESTART",
   "flags": [
    "unlock",
    "inferred"
   ]
  },
  {
   "from": "sw:110",
   "to": "sw:111",
   "kind": "normal"
  },
  {
   "from": "sw:111",
   "to": "sw:112_chapter21",
   "kind": "normal"
  },
  {
   "from": "sw:112",
   "to": "sw:112_01com",
   "kind": "merge"
  },
  {
   "from": "sw:112_01B",
   "to": "sw:112_01com",
   "kind": "merge"
  },
  {
   "from": "sw:112_01com",
   "to": "sw:113",
   "kind": "normal"
  },
  {
   "from": "sw:112_chapter21",
   "to": "sw:112#opt1-0",
   "kind": "branch",
   "order": 0,
   "label": "このまま中に",
   "effects": [
    {
     "var": "sel_flag",
     "op": "set",
     "value": 0
    }
   ]
  },
  {
   "from": "sw:112_chapter21",
   "to": "sw:112#opt1-1",
   "kind": "branch",
   "order": 1,
   "label": "このまま外に",
   "effects": [
    {
     "var": "sel_flag",
     "op": "set",
     "value": 1
    }
   ]
  },
  {
   "from": "sw:112#opt1-0",
   "to": "sw:112_dummy1",
   "kind": "merge"
  },
  {
   "from": "sw:112#opt1-1",
   "to": "sw:112_dummy1",
   "kind": "merge"
  },
  {
   "from": "sw:112_dummy1",
   "to": "sw:112_chapter21",
   "kind": "back"
  },
  {
   "from": "sw:112_dummy1",
   "to": "sw:112_dummy1#cond",
   "kind": "normal"
  },
  {
   "from": "sw:112_dummy1#cond",
   "to": "sw:112",
   "kind": "branch",
   "order": 0,
   "label": "例：sel_flag=0"
  },
  {
   "from": "sw:112_dummy1#cond",
   "to": "sw:112_01B",
   "kind": "branch",
   "order": 1,
   "label": "例：sel_flag=1"
  },
  {
   "from": "sw:113",
   "to": "sw:113_01A",
   "kind": "branch",
   "order": 0,
   "label": "わかった",
   "effects": [
    {
     "var": "eye_flag",
     "op": "set",
     "value": 0
    }
   ]
  },
  {
   "from": "sw:113",
   "to": "sw:113_01B",
   "kind": "branch",
   "order": 1,
   "label": "オレはそんなこと気にしない"
  },
  {
   "from": "sw:113_01A",
   "to": "sw:113",
   "kind": "back"
  },
  {
   "from": "sw:113_01A",
   "to": "sw:113_01com",
   "kind": "merge"
  },
  {
   "from": "sw:113_01B",
   "to": "sw:113_01com",
   "kind": "merge"
  },
  {
   "from": "sw:113_01com",
   "to": "sw:113_01com#cond",
   "kind": "normal"
  },
  {
   "from": "sw:113_01com#cond",
   "to": "sw:113_01A_eye",
   "kind": "branch",
   "order": 0
  },
  {
   "from": "sw:113_01com#cond",
   "to": "sw:113_01B_eye",
   "kind": "branch",
   "order": 1
  },
  {
   "from": "sw:113_01A_eye",
   "to": "sw:113_01com_eye",
   "kind": "normal"
  },
  {
   "from": "sw:113_01B_eye",
   "to": "sw:113_01com_eye",
   "kind": "normal"
  },
  {
   "from": "sw:113_01com_eye",
   "to": "sw:113#opt2-0",
   "kind": "branch",
   "order": 0,
   "label": "このまま中で一気に",
   "effects": [
    {
     "var": "sel_flag",
     "op": "set",
     "value": 0
    }
   ]
  },
  {
   "from": "sw:113_01com_eye",
   "to": "sw:113#opt2-1",
   "kind": "branch",
   "order": 1,
   "label": "寧々の身体にぶちまける",
   "effects": [
    {
     "var": "sel_flag",
     "op": "set",
     "value": 1
    }
   ]
  },
  {
   "from": "sw:113#opt2-0",
   "to": "sw:113_dummy2",
   "kind": "merge"
  },
  {
   "from": "sw:113#opt2-1",
   "to": "sw:113_dummy2",
   "kind": "merge"
  },
  {
   "from": "sw:113_dummy2",
   "to": "sw:113_dummy2#cond",
   "kind": "normal"
  },
  {
   "from": "sw:113_dummy2#cond",
   "to": "sw:113_02A",
   "kind": "branch",
   "order": 0,
   "label": "例：eye_flag=0, sel_flag=0"
  },
  {
   "from": "sw:113_dummy2#cond",
   "to": "sw:113_02B",
   "kind": "branch",
   "order": 1,
   "label": "例：eye_flag=0, sel_flag=1"
  },
  {
   "from": "sw:113_02A",
   "to": "sw:113_02com",
   "kind": "merge"
  },
  {
   "from": "sw:113_02com",
   "to": "sw:113_02com#cond",
   "kind": "normal"
  },
  {
   "from": "sw:113_02com#cond",
   "to": "sw:113_02A_eye",
   "kind": "branch",
   "order": 0
  },
  {
   "from": "sw:113_02com#cond",
   "to": "sw:113_02B_eye",
   "kind": "branch",
   "order": 1
  },
  {
   "from": "sw:113_02A_eye",
   "to": "sw:113_02com_eye",
   "kind": "merge"
  },
  {
   "from": "sw:113_02B",
   "to": "sw:113_02com",
   "kind": "merge"
  },
  {
   "from": "sw:113_02B_eye",
   "to": "sw:113_02com_eye",
   "kind": "merge"
  },
  {
   "from": "sw:113_02com_eye",
   "to": "sw:114",
   "kind": "normal"
  },
  {
   "from": "sw:114",
   "to": "sw:114_chapter31",
   "kind": "normal"
  },
  {
   "from": "sw:114_chapter31",
   "to": "sw:115_chapter35",
   "kind": "normal"
  },
  {
   "from": "sw:115",
   "to": "sw:115_01com",
   "kind": "normal"
  },
  {
   "from": "sw:115_01B",
   "to": "sw:115_01com",
   "kind": "normal"
  },
  {
   "from": "sw:115_01com",
   "to": "sw:115_02A",
   "kind": "branch",
   "order": 0,
   "label": "このままイく",
   "effects": [
    {
     "var": "sel_flag",
     "op": "set",
     "value": 0
    }
   ]
  },
  {
   "from": "sw:115_01com",
   "to": "sw:115_02B",
   "kind": "branch",
   "order": 1,
   "label": "最後に自分で動いてイく",
   "effects": [
    {
     "var": "sel_flag",
     "op": "set",
     "value": 1
    }
   ]
  },
  {
   "from": "sw:115_02A",
   "to": "sw:115_02com",
   "kind": "merge"
  },
  {
   "from": "sw:115_02B",
   "to": "sw:115_02com",
   "kind": "merge"
  },
  {
   "from": "sw:115_02com",
   "to": "sw:116",
   "kind": "normal"
  },
  {
   "from": "sw:115_chapter35",
   "to": "sw:115#opt1-0",
   "kind": "branch",
   "order": 0,
   "label": "このまま中で一気に",
   "effects": [
    {
     "var": "sel_flag",
     "op": "set",
     "value": 0
    }
   ]
  },
  {
   "from": "sw:115_chapter35",
   "to": "sw:115#opt1-1",
   "kind": "branch",
   "order": 1,
   "label": "寧々の身体にぶちまける",
   "effects": [
    {
     "var": "sel_flag",
     "op": "set",
     "value": 1
    }
   ]
  },
  {
   "from": "sw:115#opt1-0",
   "to": "sw:115_dummy1",
   "kind": "merge"
  },
  {
   "from": "sw:115#opt1-1",
   "to": "sw:115_dummy1",
   "kind": "merge"
  },
  {
   "from": "sw:115_dummy1",
   "to": "sw:115_chapter35",
   "kind": "back"
  },
  {
   "from": "sw:115_dummy1",
   "to": "sw:115_dummy1#cond",
   "kind": "normal"
  },
  {
   "from": "sw:115_dummy1#cond",
   "to": "sw:115",
   "kind": "branch",
   "order": 0,
   "label": "例：sel_flag=0"
  },
  {
   "from": "sw:115_dummy1#cond",
   "to": "sw:115_01B",
   "kind": "branch",
   "order": 1,
   "label": "例：sel_flag=1"
  },
  {
   "from": "sw:116",
   "to": "sw:117",
   "kind": "normal",
   "label": "通關後 After Story",
   "flags": [
    "unlock"
   ]
  },
  {
   "from": "sw:500",
   "to": "sw:501",
   "kind": "normal"
  },
  {
   "from": "sw:501",
   "to": "sw:502",
   "kind": "normal"
  },
  {
   "from": "sw:502",
   "to": "sw:503",
   "kind": "normal"
  },
  {
   "from": "sw:503",
   "to": "sw:504",
   "kind": "normal"
  },
  {
   "from": "sw:504",
   "to": "sw:505",
   "kind": "normal"
  },
  {
   "from": "sw:505",
   "to": "sw:506",
   "kind": "normal"
  },
  {
   "from": "sw:506",
   "to": "sw:506_01A",
   "kind": "branch",
   "order": 0,
   "label": "口で受け止めて欲しい",
   "effects": [
    {
     "var": "sel_flag",
     "op": "set",
     "value": 0
    }
   ]
  },
  {
   "from": "sw:506",
   "to": "sw:506_01B",
   "kind": "branch",
   "order": 1,
   "label": "顔で受け止めて欲しい",
   "effects": [
    {
     "var": "sel_flag",
     "op": "set",
     "value": 1
    }
   ]
  },
  {
   "from": "sw:506_01A",
   "to": "sw:506_01com",
   "kind": "normal"
  },
  {
   "from": "sw:506_01com",
   "to": "sw:506_02A",
   "kind": "branch",
   "order": 0,
   "label": "腰をずらして勝負",
   "effects": [
    {
     "var": "sel_flag",
     "op": "set",
     "value": 1
    }
   ]
  },
  {
   "from": "sw:506_01com",
   "to": "sw:506_02B",
   "kind": "branch",
   "order": 1,
   "label": "このまま真っ向勝負",
   "effects": [
    {
     "var": "sel_flag",
     "op": "set",
     "value": 0
    }
   ]
  },
  {
   "from": "sw:506_02A",
   "to": "sw:506_02com",
   "kind": "normal"
  },
  {
   "from": "sw:506_02com",
   "to": "sw:506#opt3-0",
   "kind": "branch",
   "order": 0,
   "label": "和奏の中に全てを解き放つ！",
   "effects": [
    {
     "var": "sel_flag",
     "op": "set",
     "value": 0
    }
   ]
  },
  {
   "from": "sw:506_02com",
   "to": "sw:506#opt3-1",
   "kind": "branch",
   "order": 1,
   "label": "和奏の身体にかけたい！",
   "effects": [
    {
     "var": "sel_flag",
     "op": "set",
     "value": 1
    }
   ]
  },
  {
   "from": "sw:506#opt3-0",
   "to": "sw:506_dummy1",
   "kind": "merge"
  },
  {
   "from": "sw:506#opt3-1",
   "to": "sw:506_dummy1",
   "kind": "merge"
  },
  {
   "from": "sw:506_02B",
   "to": "sw:506_02com",
   "kind": "normal"
  },
  {
   "from": "sw:506_01B",
   "to": "sw:506_01com",
   "kind": "normal"
  },
  {
   "from": "sw:506_dummy1",
   "to": "sw:506_dummy1#cond",
   "kind": "normal"
  },
  {
   "from": "sw:506_dummy1#cond",
   "to": "sw:506_03A",
   "kind": "branch",
   "order": 0,
   "label": "例：sel_flag=0"
  },
  {
   "from": "sw:506_dummy1#cond",
   "to": "sw:506_03B",
   "kind": "branch",
   "order": 1,
   "label": "例：sel_flag=1"
  },
  {
   "from": "sw:506_03A",
   "to": "sw:506_03com",
   "kind": "merge"
  },
  {
   "from": "sw:506_03B",
   "to": "sw:506_03com",
   "kind": "merge"
  },
  {
   "from": "sw:506_03com",
   "to": "sw:507",
   "kind": "normal"
  },
  {
   "from": "sw:507",
   "to": "sw:508",
   "kind": "normal",
   "label": "通關後 After Story",
   "flags": [
    "unlock"
   ]
  },
  {
   "from": "sw:117",
   "to": "sw:117_01A",
   "kind": "branch",
   "order": 0,
   "label": "いや、我慢できるはず！",
   "effects": [
    {
     "var": "sel_flag",
     "op": "set",
     "value": 0
    }
   ]
  },
  {
   "from": "sw:117",
   "to": "sw:117_01B",
   "kind": "branch",
   "order": 1,
   "label": "やっぱりもう無理だ！",
   "effects": [
    {
     "var": "sel_flag",
     "op": "set",
     "value": 1
    }
   ]
  },
  {
   "from": "sw:117_01A",
   "to": "sw:117_01com",
   "kind": "merge"
  },
  {
   "from": "sw:117_01B",
   "to": "sw:117_01com",
   "kind": "merge"
  },
  {
   "from": "sw:117_01com",
   "to": "sw:117_01com#cond",
   "kind": "normal"
  },
  {
   "from": "sw:117_01com#cond",
   "to": "sw:117_02A",
   "kind": "branch",
   "order": 0
  },
  {
   "from": "sw:117_01com#cond",
   "to": "sw:117_02B",
   "kind": "branch",
   "order": 1
  },
  {
   "from": "sw:117_02A",
   "to": "sw:117_02com",
   "kind": "normal"
  },
  {
   "from": "sw:117_02B",
   "to": "sw:117_02com",
   "kind": "normal"
  },
  {
   "from": "sw:117_02com",
   "to": "sw:117#opt2-0",
   "kind": "branch",
   "order": 0,
   "label": "このまま出す！",
   "effects": [
    {
     "var": "sel_flag",
     "op": "set",
     "value": 0
    }
   ]
  },
  {
   "from": "sw:117_02com",
   "to": "sw:117#opt2-1",
   "kind": "branch",
   "order": 1,
   "label": "少し慎重になる",
   "effects": [
    {
     "var": "sel_flag",
     "op": "set",
     "value": 1
    }
   ]
  },
  {
   "from": "sw:117#opt2-0",
   "to": "sw:117_dummy1",
   "kind": "merge"
  },
  {
   "from": "sw:117#opt2-1",
   "to": "sw:117_dummy1",
   "kind": "merge"
  },
  {
   "from": "sw:117_dummy1",
   "to": "sw:117_dummy1#cond",
   "kind": "normal"
  },
  {
   "from": "sw:117_dummy1#cond",
   "to": "sw:117_03A",
   "kind": "branch",
   "order": 0,
   "label": "例：sel_flag=0"
  },
  {
   "from": "sw:117_dummy1#cond",
   "to": "sw:117_03B",
   "kind": "branch",
   "order": 1,
   "label": "例：sel_flag=1"
  },
  {
   "from": "sw:117_03A",
   "to": "sw:117_03com",
   "kind": "merge"
  },
  {
   "from": "sw:117_03B",
   "to": "sw:117_03com",
   "kind": "merge"
  },
  {
   "from": "sw:508",
   "to": "sw:508_01A",
   "kind": "branch",
   "order": 0,
   "label": "素直に従う",
   "effects": [
    {
     "var": "sel_flag",
     "op": "set",
     "value": 0
    }
   ]
  },
  {
   "from": "sw:508",
   "to": "sw:508_01B",
   "kind": "branch",
   "order": 1,
   "label": "少しは抵抗する",
   "effects": [
    {
     "var": "sel_flag",
     "op": "set",
     "value": 1
    }
   ]
  },
  {
   "from": "sw:508_01A",
   "to": "sw:508_01com",
   "kind": "normal"
  },
  {
   "from": "sw:508_01B",
   "to": "sw:508_01com",
   "kind": "normal"
  },
  {
   "from": "sw:508_01com",
   "to": "sw:508_02A",
   "kind": "branch",
   "order": 0,
   "label": "舐められる方",
   "effects": [
    {
     "var": "sel_flag",
     "op": "set",
     "value": 1
    }
   ]
  },
  {
   "from": "sw:508_01com",
   "to": "sw:508_02B",
   "kind": "branch",
   "order": 1,
   "label": "しゃぶられる方",
   "effects": [
    {
     "var": "sel_flag",
     "op": "set",
     "value": 0
    }
   ]
  },
  {
   "from": "sw:508_02A",
   "to": "sw:508_01com",
   "kind": "back"
  },
  {
   "from": "sw:508_02A",
   "to": "sw:508_02com",
   "kind": "merge"
  },
  {
   "from": "sw:508_02B",
   "to": "sw:508_02com",
   "kind": "merge"
  }
 ]
}
