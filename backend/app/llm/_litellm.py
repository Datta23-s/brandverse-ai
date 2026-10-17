"""The one import of LiteLLM (Rules.md P6: every model call goes through app/llm/gateway.py).

Importing LiteLLM fetches its model price map from the network unless told not to. The project
never uses LiteLLM's prices (cost comes from config/models.yaml), and tests must make no external
call (Rules.md §6), so the bundled map is selected BEFORE the import.
"""

import os

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

import litellm  # noqa: E402 — must follow the environment switch above

litellm.suppress_debug_info = True

acompletion = litellm.acompletion
from litellm.exceptions import RateLimitError  # noqa: E402

__all__ = ["RateLimitError", "acompletion"]
