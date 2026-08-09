"""
例外處理模組 (Exceptions)。

Exceptions package.

統一匯出所有自定義例外，方便其他模組使用。

Re-exports all custom exceptions in one place for convenient imports.
"""

from .base import FluencyTidesError
from .infrastructure import (
    InfrastructureBaseError,
    LLMServiceError,
    AnkiServiceError,
    StorageServiceError,
)
from .bot import (
    BotBaseError,
    BotStateError,
    BotInputError,
    BotActionError,
)
from .services import (
    ServiceBaseError,
    DuplicateCardError,
    DeckNotFoundError,
    ModelFileNotFoundError,
    PromptTemplateNotFoundError,
    AuthenticationError,
    LLMGenerationError,
    ClozePositioningError,
)

__all__ = [
    "FluencyTidesError",
    "InfrastructureBaseError",
    "LLMServiceError",
    "AnkiServiceError",
    "StorageServiceError",
    "BotBaseError",
    "BotStateError",
    "BotInputError",
    "BotActionError",
    "ServiceBaseError",
    "DuplicateCardError",
    "DeckNotFoundError",
    "ModelFileNotFoundError",
    "PromptTemplateNotFoundError",
    "AuthenticationError",
    "LLMGenerationError",
    "ClozePositioningError",
]
