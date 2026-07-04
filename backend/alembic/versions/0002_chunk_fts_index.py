"""add GIN full-text index on chunks.content for hybrid/sparse retrieval (RET-01)

Revision ID: 0002_chunk_fts_index
Revises: 0001_create_core_schema
Create Date: 2026-07-03
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0002_chunk_fts_index"
down_revision: Union[str, Sequence[str], None] = "0001_create_core_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_chunks_content_fts "
        "ON chunks USING gin (to_tsvector('english', content));"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_chunks_content_fts;")
