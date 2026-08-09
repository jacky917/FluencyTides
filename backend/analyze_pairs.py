import asyncio
import sys
import json
from pathlib import Path
from pydantic import BaseModel, Field

# Add backend to path
backend_dir = Path(r'c:\Users\forip\Desktop\WorkSpace\Python\FluencyTides\backend')
sys.path.insert(0, str(backend_dir))

from app.infrastructure.llm.client import LLMClient

class ProblematicPair(BaseModel):
    id: int = Field(..., description="ID of the note")
    in_word: str = Field(..., description="Intransitive word")
    tr_word: str = Field(..., description="Transitive word")
    issue: str = Field(..., description="Explanation of why this pair is problematic")

class AnalysisResult(BaseModel):
    problematic_pairs: list[ProblematicPair]

async def main():
    llm = LLMClient()
    
    with open('anki_verb_pairs.json', 'r', encoding='utf-8') as f:
        pairs = json.load(f)
        
    prompt = f"Analyze the following {len(pairs)} Japanese verb pairs (Intransitive vs Transitive). Identify pairs that fall under these categories:\n" \
             f"1. Swapped: The Intransitive verb is actually Transitive, and vice versa (e.g. 浴びせる/浴びる).\n" \
             f"2. Not a valid pair: They are not considered a standard Intransitive/Transitive pair (e.g. 捏ねる/熟す, 来たる/来たす).\n" \
             f"3. Both are transitive or both are intransitive.\n" \
             f"4. Completely same meaning/Kanji with weird readings, or one is completely unrelated.\n" \
             f"Return ONLY the problematic pairs and briefly explain the issue.\n\n" \
             f"Pairs to analyze (Format: ID | Intransitive | Transitive):\n"
             
    for p in pairs:
        prompt += f"{p['id']} | {p['in_word']} | {p['tr_word']}\n"
        
    print("Sending to LLM...")
    
    try:
        response = await llm.generate_structured_data(
            system_prompt="You are a Japanese linguistics expert.",
            user_prompt=prompt,
            response_schema=AnalysisResult
        )
        with open('problematic_pairs.json', 'w', encoding='utf-8') as f:
            json.dump(response.model_dump(), f, ensure_ascii=False, indent=2)
        print("Analysis complete. Check problematic_pairs.json")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == '__main__':
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
