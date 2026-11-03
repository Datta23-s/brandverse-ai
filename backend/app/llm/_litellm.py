"""The one import of LiteLLM (Rules.md P6: every model call goes through app/llm/gateway.py).

Importing LiteLLM fetches its model price map from the network unless told not to. The project
never uses LiteLLM's prices (cost comes from config/models.yaml), and tests must make no external
call (Rules.md §6), so the bundled map is selected BEFORE the import.
"""

import os

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
# LiteLLM calls `dotenv.load_dotenv()` at import unless LITELLM_MODE is not DEV, copying the
# repository's `.env` (GOOGLE_API_KEY among it) into os.environ. Settings reads the process
# environment even with `_env_file=None`, so every test that imported this module held a live key
# (found 3 November 2026) while CI, which has no `.env`, did not. The application reads `.env`
# through Settings only (Rules.md 3.1); LiteLLM must not.
os.environ.setdefault("LITELLM_MODE", "PRODUCTION")

import litellm  # noqa: E402 — must follow the environment switch above

litellm.suppress_debug_info = True

acompletion = litellm.acompletion
from litellm.exceptions import APIConnectionError, RateLimitError  # noqa: E402

__all__ = ["APIConnectionError", "RateLimitError", "acompletion"]
