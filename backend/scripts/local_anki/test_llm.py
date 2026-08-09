"""LLM 連線診斷腳本：驗證 .env 中的 LLM 端點與模型身分。

LLM connectivity diagnostic script: verifies the LLM endpoint and
model identity configured in .env by sending a probe request.
"""

import sys
import asyncio
from pathlib import Path

# 強制指向 backend 資料夾以正確解析模組
_backend_dir = Path(__file__).resolve().parents[2]
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))
import scripts.common.env  # noqa

from app.core.config import settings
from openai import AsyncOpenAI

async def main():
    """腳本進入點：發送診斷提示詞並印出 LLM 的自我身分回覆。

    Script entry point: send a diagnostic prompt and print the LLM's
    self-reported identity to detect proxy/model mismatches.
    """
    print(f"LLM_BASE_URL: {settings.LLM_BASE_URL}")
    print(f"LLM_MODEL_NAME: {settings.LLM_MODEL_NAME}")
    print("Sending test request to LLM...")
    
    try:
        client = AsyncOpenAI(
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_BASE_URL,
        )
        
        diagnostic_prompt = f"""請以系統診斷工具的身分回答。
我目前發送請求的 API 節點 (Base URL) 是：{settings.LLM_BASE_URL}
我請求的模型名稱 (Model Name) 是：{settings.LLM_MODEL_NAME}

請根據你的內部知識，告訴我你「真正」的身分：
1. 你是由哪家公司開發的？
2. 你的具體模型家族與版本是什麼？(例如 GPT-4o, Claude 3.5 Sonnet, Gemini 1.5 Pro 等)
3. 如果我請求的模型與你的真實身分不符，請告訴我你可能被 API 代理 (Proxy) 替換了。"""

        response = await client.chat.completions.create(
            model=settings.LLM_MODEL_NAME,
            messages=[
                {"role": "user", "content": diagnostic_prompt}
            ],
            temperature=0.0
        )
        
        reply = response.choices[0].message.content
        print("\n[SUCCESS]")
        print("-" * 30)
        if reply is None:
            print("Message content is None! Printing raw choice to debug:")
            print(response.choices[0].model_dump_json(indent=2))
        else:
            print(reply)
        print("-" * 30)
        
    except Exception as e:
        print(f"\n[FAILED]: {e}")

if __name__ == "__main__":
    asyncio.run(main())
