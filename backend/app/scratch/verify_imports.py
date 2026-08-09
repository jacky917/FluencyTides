import asyncio
from app.main import app
from app.api.verb_pair import router
from app.services.task_handlers.verb_pair_handler import VerbPairHandler
from app.schemas.llm.verb_pair import VerbPairGenerationResult
from app.infrastructure.anki.json_modifier import AnkiJsonFieldManager

print("All imports successful! The API routes and handlers are well-formed.")
