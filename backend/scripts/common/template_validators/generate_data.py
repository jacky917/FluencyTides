"""產生 Speaking Coach 驗證器所需的 data_ja.json 假場景資料檔。

Generate the data_ja.json fake scenario data file used by the Speaking
Coach validator.
"""

import json
from pathlib import Path

scenarios = [
    {
        "card_id": "test_speaking_001",
        "prompt": "今日、プロジェクトの締め切りお疲れ様！この後、駅前のカフェで何か奢るよ。ちょっとお茶でもしていく？",
        "context": "仕事の締め切りが終わり、先輩がカフェに誘ってくれた。あなたはこれに応えるか、断るかを選択する。",
        "prompt_audios": [
            {"avatar": "example1.jpg", "audio": "example1.wav", "speaker": "先輩A"},
            {"avatar": "example2.jpg", "audio": "example2.wav", "speaker": "先輩B"}
        ],
        "recordings": [
            {
                "date": "2026-06-01",
                "audio": "speaker.wav",
                "transcript": "ありがとうございます！ぜひお願いします。",
                "comment": "簡潔で自然な返答です。",
                "score": 90
            }
        ],
        "references": [
            {
                "status": 1,
                "date": "2026-06-01",
                "content": "ありがとうございます！ぜひご一緒させてください。",
                "audios": [{"avatar": "example1.jpg", "audio": "example1.wav", "speaker": "ネイティブA"}]
            },
            {
                "status": 1,
                "date": "2026-06-01",
                "content": "お気持ちはとても嬉しいのですが、この後どうしても外せない予定がありまして…。また改めてぜひお願いします！",
                "audios": [{"avatar": "example2.jpg", "audio": "example2.wav", "speaker": "ネイティブB"}]
            }
        ]
    },
    {
        "card_id": "test_speaking_002",
        "prompt": "ご注文はお決まりでしょうか？",
        "context": "居酒屋の店員が注文を取りに来た。",
        "prompt_audios": [
            {"avatar": "example2.jpg", "audio": "example2.wav", "speaker": "店員"}
        ],
        "recordings": [],
        "references": [
            {
                "status": 1,
                "date": "2026-06-01",
                "content": "はい、とりあえず生ビールを二つお願いします。",
                "audios": []
            },
            {
                "status": 1,
                "date": "2026-06-01",
                "content": "すみません、もう少し時間をいただけますか？",
                "audios": []
            }
        ]
    },
    {
        "card_id": "test_speaking_003",
        "prompt": "明日からの出張の件なんだけど、準備は進んでる？",
        "context": "上司に明日の出張の準備状況を聞かれた。",
        "prompt_audios": [
            {"avatar": "example1.jpg", "audio": "example1.wav", "speaker": "上司"}
        ],
        "recordings": [],
        "references": [
            {
                "status": 1,
                "date": "2026-06-01",
                "content": "はい、資料の印刷も終わり、すべて順調に進んでおります。",
                "audios": []
            },
            {
                "status": 1,
                "date": "2026-06-01",
                "content": "申し訳ありません、一部の資料がまだ完成しておらず、午後には終わらせる予定です。",
                "audios": []
            }
        ]
    },
    {
        "card_id": "test_speaking_004",
        "prompt": "どうされましたか？道に迷われましたか？",
        "context": "駅で迷っていたら、駅員が声をかけてくれた。",
        "prompt_audios": [
            {"avatar": "example2.jpg", "audio": "example2.wav", "speaker": "駅員"}
        ],
        "recordings": [],
        "references": [
            {
                "status": 1,
                "date": "2026-06-01",
                "content": "はい、新宿駅に行きたいのですが、どの電車に乗ればいいのか分からなくて…。",
                "audios": []
            },
            {
                "status": 1,
                "date": "2026-06-01",
                "content": "大丈夫です、ただスマホで地図を確認しているだけです。ありがとうございます。",
                "audios": []
            }
        ]
    },
    {
        "card_id": "test_speaking_005",
        "prompt": "今週末、みんなでバーベキューするんだけど、来ない？",
        "context": "友達から週末のバーベキューに誘われた。",
        "prompt_audios": [
            {"avatar": "example1.jpg", "audio": "example1.wav", "speaker": "友達"}
        ],
        "recordings": [],
        "references": [
            {
                "status": 1,
                "date": "2026-06-01",
                "content": "行く行く！何か持っていくものはある？",
                "audios": []
            },
            {
                "status": 1,
                "date": "2026-06-01",
                "content": "ごめん、その日はもう別の予定が入ってて…。また今度誘って！",
                "audios": []
            }
        ]
    },
    {
        "card_id": "test_speaking_006",
        "prompt": "それでは、簡単に自己紹介をお願いできますか。",
        "context": "面接官に自己紹介を求められた。",
        "prompt_audios": [
            {"avatar": "example2.jpg", "audio": "example2.wav", "speaker": "面接官"}
        ],
        "recordings": [],
        "references": [
            {
                "status": 1,
                "date": "2026-06-01",
                "content": "はい、〇〇と申します。前職では営業として〇年間勤務しておりました。本日はよろしくお願いいたします。",
                "audios": []
            },
            {
                "status": 0,
                "date": "2026-06-01",
                "content": "えっと、〇〇です。趣味はゲームです。よろしくです。",
                "audios": []
            }
        ]
    },
    {
        "card_id": "test_speaking_007",
        "prompt": "いらっしゃいませ、ご予約のお客様ですね。お名前をお願いいたします。",
        "context": "ホテルのフロントでチェックインをする。",
        "prompt_audios": [
            {"avatar": "example1.jpg", "audio": "example1.wav", "speaker": "フロント"}
        ],
        "recordings": [],
        "references": [
            {
                "status": 1,
                "date": "2026-06-01",
                "content": "はい、予約した田中です。よろしくお願いいたします。",
                "audios": []
            },
            {
                "status": 1,
                "date": "2026-06-01",
                "content": "すみません、予約はしていないのですが、本日空き部屋はありますか？",
                "audios": []
            }
        ]
    },
    {
        "card_id": "test_speaking_008",
        "prompt": "レジ袋はご利用になりますか？",
        "context": "コンビニのレジで袋が必要か聞かれた。",
        "prompt_audios": [
            {"avatar": "example2.jpg", "audio": "example2.wav", "speaker": "店員"}
        ],
        "recordings": [],
        "references": [
            {
                "status": 1,
                "date": "2026-06-01",
                "content": "はい、一枚お願いします。",
                "audios": []
            },
            {
                "status": 1,
                "date": "2026-06-01",
                "content": "大丈夫です、そのままカバンに入れます。",
                "audios": []
            }
        ]
    },
    {
        "card_id": "test_speaking_009",
        "prompt": "もしもし、昨日納品された商品なんですが、数が足りないようです。",
        "context": "顧客からクレームの電話がかかってきた。",
        "prompt_audios": [
            {"avatar": "example1.jpg", "audio": "example1.wav", "speaker": "顧客"}
        ],
        "recordings": [],
        "references": [
            {
                "status": 1,
                "date": "2026-06-01",
                "content": "大変申し訳ございません。至急確認し、足りない分をすぐにお送りいたします。",
                "audios": []
            },
            {
                "status": 1,
                "date": "2026-06-01",
                "content": "ご不便をおかけして申し訳ありません。すぐに配送担当に確認して折り返しご連絡いたします。",
                "audios": []
            }
        ]
    },
    {
        "card_id": "test_speaking_010",
        "prompt": "こちらのシャツ、いかがですか？ご試着もできますよ。",
        "context": "アパレルショップで店員に声をかけられた。",
        "prompt_audios": [
            {"avatar": "example2.jpg", "audio": "example2.wav", "speaker": "店員"}
        ],
        "recordings": [],
        "references": [
            {
                "status": 1,
                "date": "2026-06-01",
                "content": "ありがとうございます。では、こちらを試着させていただいてもいいですか？",
                "audios": []
            },
            {
                "status": 1,
                "date": "2026-06-01",
                "content": "ありがとうございます、今はただ見ているだけなので大丈夫です。",
                "audios": []
            }
        ]
    }
]

output_path = Path(__file__).resolve().parent / "data_ja.json"
with open(output_path, "w", encoding="utf-8") as f:
    json.dump(scenarios, f, ensure_ascii=False, indent=2)

print(f"Generated {output_path}")
