"""ingestion platform: sources + ingestion_runs + document provenance (ING-01/02/07)

Revision ID: 0007_ingestion_sources
Revises: 0006_identity_rbac
Create Date: 2026-07-04
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0007_ingestion_sources"
down_revision: Union[str, Sequence[str], None] = "0006_identity_rbac"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("collection_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("collections.id", ondelete="CASCADE"), nullable=True),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("external_ref", sa.String(length=1024), nullable=True),
        sa.Column("config", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("collection_id", "source_type", "display_name",
                            name="uq_source_collection_type_name"),
    )
    op.create_index("ix_sources_tenant_collection", "sources", ["tenant_id", "collection_id"])

    op.create_table(
        "ingestion_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("collection_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("collections.id", ondelete="CASCADE"), nullable=True),
        sa.Column("triggered_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("trigger_type", sa.String(length=16), nullable=False, server_default="manual"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="queued"),
        sa.Column("documents_seen", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("documents_indexed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("documents_quarantined", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("chunks_created", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("chunks_reused", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("run_metadata", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_ingestion_runs_source", "ingestion_runs", ["source_id", "created_at"])
    op.create_index("ix_ingestion_runs_tenant_collection", "ingestion_runs",
                    ["tenant_id", "collection_id"])

    op.add_column("documents", sa.Column("source_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("sources.id", ondelete="SET NULL"), nullable=True))
    op.add_column("documents", sa.Column("ingestion_run_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("ingestion_runs.id", ondelete="SET NULL"), nullable=True))
    op.add_column("documents", sa.Column("created_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "created_by")
    op.drop_column("documents", "ingestion_run_id")
    op.drop_column("documents", "source_id")
    op.drop_index("ix_ingestion_runs_tenant_collection", table_name="ingestion_runs")
    op.drop_index("ix_ingestion_runs_source", table_name="ingestion_runs")
    op.drop_table("ingestion_runs")
    op.drop_index("ix_sources_tenant_collection", table_name="sources")
    op.drop_table("sources")
