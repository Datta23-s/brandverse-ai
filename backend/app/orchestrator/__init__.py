"""The LangGraph campaign pipeline (Architecture.md §5; Phases.md Phase 6 chunk A).

Importing this package sets the two environment switches LangGraph reads at import time, before any
module of it is imported:

- `LANGGRAPH_STRICT_MSGPACK=true` (Architecture.md §5.3): the checkpoint serializer refuses types it
  was not told to allow, so everything in `RunState` is plain data (str, int, float, bool, None,
  list, dict). UUIDs travel as strings.
- tracing off: `langchain-core` and `langsmith` come in as LangGraph's dependencies. No key is
  configured anywhere, and this makes sure nothing is sent to a tracing service if one ever is.
"""

import os

os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")
os.environ.setdefault("LANGCHAIN_TRACING_V2", "false")
os.environ.setdefault("LANGSMITH_TRACING", "false")
