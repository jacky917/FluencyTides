"""What goes into each person card field, for the LLM that organizes a description (field routing plan D2).

人脈卡每個欄位放什麼（給整理一段描述的 LLM 看；欄位分工計畫書 D2）。

This is the single source of "what goes where": the prompt ``Person_Profile_Ingest.j2`` renders its routing table from
these entries, so adding a field means adding its guide here, not editing the prompt's prose.
:mod:`app.services.person_profile_ingest` checks at import time that the guides cover exactly the history fields
and the updatable text fields (a missing or a stale entry both fail), that every field with event times explains
each time, and that every field with a note explains the note. The guides live in services, not in the schemas:
they are wording for the LLM, kept apart from the field definitions and the form descriptions people read.

這裡是「什麼放哪裡」唯一的來源：提示詞 ``Person_Profile_Ingest.j2`` 的分工表由這些說明產生，加欄位時在這裡加說明，
不必改提示詞的文字。``person_profile_ingest`` 在 import 時檢查：說明剛好涵蓋會變的欄位與可更新的文字欄位（少了、
多了都會失敗）、有事件時間的欄位每個時間都有說明、有說明鍵的欄位說明鍵也有說明。說明放在 services 而不是 schemas：
它們是給 LLM 的用語，和欄位定義、給人看的表單說明分開。

The guides are for the LLM, not for people: the create form's descriptions (``_rows(title, description)``, such as
「輸入後按 Enter 加入」) are separate. Examples must not come from the descriptions used to test the prompt (plan D3),
otherwise a test with the same text no longer shows whether the rule itself works.

這些說明是給 LLM 看的，和建卡表單給人看的說明（``_rows(title, description)``，例如「輸入後按 Enter 加入」）分開。
例子不能取自實測提示詞用的描述（計畫書 D3），否則用同一段話測就看不出規則本身好不好。
"""

#: What belongs in each field (and what does not), with an example or two. A guide may refer to a section of the
#: prompt by its title (「各項的寫法」); rename both together.
#: 每個欄位放什麼（與不放什麼），附一兩個例子。說明可以引用提示詞的段落名稱（「各項的寫法」），改標題時要一起改。
FIELD_GUIDES: dict[str, str] = {
    # ---- 文字欄位 ----
    "Name_ZH": "中文姓名（文中有才填）",
    "Name_EN": "英文名，照對方的拼法",
    "Name_JA": "日文姓名（漢字），姓與名之間空一格，例如「佐藤 健太」",
    "Name_JA_Kana": "日文姓名的讀音，與日文姓名用空白對齊分段，例如「さとう けんた」",
    "Name_Romaji": "姓名的羅馬拼音",
    "Honorific": "怎麼稱呼、要不要用敬語，例如「さん／敬語」",
    "Name_Memo": "名字的由來、諧音或幫忙記住名字的聯想",
    "Appearance": "外貌特徵（認人用的線索），例如「戴圓框眼鏡、很高」",
    "Birthday": "生日",
    "Hometown": "出身地、老家",
    "Met_Date": "第一次認識的日期",
    "Met_Where": "在哪裡、什麼場合認識，例如「讀書會」「客戶的展覽」",
    "Met_Via": "怎麼認識的、經過誰，例如「朋友介紹」「LinkedIn」",
    "First_Impression": "使用者對他的第一印象",
    "Notes": "其他欄位都放不進、但值得記住的事，例如不確定屬於哪一份工作的工作細節",
    # ---- 會變的欄位 ----
    "Interests": "他喜歡、常做的活動或嗜好，寫成短標籤，一筆一個，例如「登山」「看 F1」。做過的具體事件放經歷，還想做的放目標",
    "Experiences": "他做過、去過、經歷過，值得之後再聊的事，寫成一句具體的話，例如「去過玉山」「在澳洲打工度假一年」",
    "Food_Drink": "喜歡吃喝的東西、愛去的店，例如「拉麵」「喜歡精釀啤酒」",
    "Dislikes": "討厭、在意、要避開的事，例如「排隊」「太吵的店」。吃的限制放飲食限制，不要提的話題放敏感話題",
    "Dietary": "過敏、不吃的東西、宗教或健康上的飲食限制，例如「不吃牛」「對花生過敏」",
    "Company": "一筆一份工作，正職、打工、實習都算（寫法見「各項的寫法」的工作經歷）",
    "Education": "一筆一個學校",
    "Personality_Values": "個性、價值觀、煩惱、對事情的態度，例如「很重視家人」「內向，但和熟的朋友很聊得來」",
    "Praise": "具體、真實的成就或優點，之後可以拿來稱讚，例如「一個人帶起新部門」",
    "Ask_Advice": "他擅長、可以請教他的事（文中說到才記，不要從經歷自己推論），例如「轉職」「做簡報」",
    "Needs": "他正在找的人、資訊或機會，使用者可能幫得上，例如「在找會說英文的會計師」",
    "Common_Ground": "使用者和他的共同點：同鄉、同校、共同的興趣或經歷（文中說到兩人都有才算）",
    "Comm_Prefs": "偏好的聯絡管道、時段、風格，例如「平日晚上才回 LINE」",
    "Residence": "現在住在哪，例如「橫濱」「中野區」",
    "Daily_Life": (
        "生活的事實：通勤路線、常去的地方、住家或公司附近的生活圈、作息，一件一筆，例如「每天搭東急東橫線通勤」"
        "「住家附近常去的超市是 OK ストア」「每天 7 點出門」。活動本身是興趣（「游泳」放興趣），固定的時間、地點、路線"
        "才放這裡（「週末早上去區民泳池」）；住在哪放現居地，吃喝與愛去的店放飲食喜好"
    ),
    "Languages": "會說的語言與程度、語言檢定，例如「日文（N2）」「英文」",
    "Credentials": (
        "已經有的身分與資格：簽證與在留資格、永住或歸化、國籍、駕照、已取得的證照、兵役，一項一筆，例如"
        "「工作簽證」「有普通駕照」「服完兵役」。還在追求的放目標，語言檢定放語言，學位放學歷"
    ),
    "Timezone": "所在的時區，例如「Asia/Tokyo」",
    "Relationship": "他和使用者的關係，例如「客戶」「朋友」「同事」",
    "Closeness": "親密度，1–5 的數字（1 最生疏、5 最親近），使用者明確表達時才建議",
    "Sensitive": (
        "不要主動提的事。健康、宗教、政治、財務等敏感資訊，只有在使用者明顯想記下、而且對之後的相處有幫助時才建議，並放在這裡"
    ),
}

#: What each event time of a field means, in the order of ``HISTORY_PERIOD_KEYS`` (the first is the LLM's
#: ``time_start``, the second its ``time_end``).
#: 有事件時間的欄位每個時間的意義，順序同 ``HISTORY_PERIOD_KEYS``（第一個是 LLM 的 ``time_start``、第二個是 ``time_end``）。
FIELD_TIME_HINTS: dict[str, tuple[str, ...]] = {
    "Company": (
        "到職年月；「三年前」這類相對說法換算到年即可",
        "以前的工作才填離職年月；已經離職但不知道何時填 `?`；現職空字串",
    ),
    "Education": ("入學", "畢業；還在讀就空字串"),
    "Experiences": ("那件事發生的時間",),
    "Praise": ("那件事發生的時間",),
    "Interests": ("從什麼時候開始",),
    "Residence": ("什麼時候搬來",),
    "Credentials": (
        "取得的時間",
        "到期的時間；文中說了到期日才填，只知道效期幾年、不知道從何時起算時寫在說明，不要自己算。"
        "續簽、換了新的：劃掉舊的那筆，再新增一筆",
    ),
}

#: What the note (``note``) of a field with notes holds.
#: 有說明鍵（``note``）的欄位，說明寫什麼。
FIELD_NOTE_HINTS: dict[str, str] = {
    "Company": "這份工作做什麼：負責的業務、產品、專案、公司在哪，例如「前端開發」「負責會員系統改版」",
    "Education": "科系、研究，例如「資工系」",
    "Credentials": "補充的細節，例如「效期 3 年」「每年要更新」",
}
