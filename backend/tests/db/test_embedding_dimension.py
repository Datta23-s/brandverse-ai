"""The configured encoder's dimension equals the vector columns it fills (Memory.md §5).

Read from the migrated schema, not from the migration text: pgvector stores a vector(n) column's
n as its type modifier. If the encoder ever changes dimension, this fails before a single row is
written with vectors the column would refuse (or, worse, that a re-created column would accept
while the Phase 0 storage measurement silently stopped applying).
"""

import pytest

from app.memory.embeddings import load_embedding_config
from tests.db.conftest import Tx

pytestmark = pytest.mark.db


@pytest.mark.parametrize(
    ("table", "column"), [("memory_items", "embedding"), ("brands", "voice_embedding")]
)
def test_the_encoder_dimension_equals_the_column_dimension(tx: Tx, table: str, column: str) -> None:
    rows = tx.execute(
        "select a.atttypmod, format_type(a.atttypid, a.atttypmod) from pg_attribute a "
        "where a.attrelid = to_regclass(%s) and a.attname = %s",
        (f"public.{table}", column),
    )
    assert rows, f"{table}.{column} not found"
    typmod, declared = rows[0]
    assert typmod == load_embedding_config().dimensions == 768, declared
