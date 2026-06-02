from abc import ABC, abstractmethod
from typing import Tuple, Dict, Any

class BaseLLMProvider(ABC):
    """Abstract base class for all LLM providers."""

    @abstractmethod
    async def generate_content(self, model_name: str, prompt: str) -> Tuple[str, Dict[str, Any]]:
        """
        Generate content using the provider.
        Returns a tuple of (result_text, usage_stats).
        usage_stats should contain:
        - prompt_tokens (int)
        - completion_tokens (int)
        - total_tokens (int)
        """
        pass
