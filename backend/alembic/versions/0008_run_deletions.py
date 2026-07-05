"""ingestion run: documents_deleted counter for connector delete detection (ING-08)

Revision ID: 0008_run_deletions
Revises: 0007_ingestion_sources
Create Date: 2026-07-04
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0008_run_deletions"
down_revision: Union[str, Sequence[str], None] = "0007_ingestion_sources"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ingestion_runs",
        sa.Column("documents_deleted", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("ingestion_runs", "documents_deleted")
