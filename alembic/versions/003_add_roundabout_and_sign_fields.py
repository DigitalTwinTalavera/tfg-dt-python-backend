"""Add is_roundabout, roundabout_id to dt_edges (Fase 1 TFG)

Revision ID: 003_roundabout_edges
Revises: 002_vehicles
Create Date: 2026-04-20

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "003_roundabout_edges"
down_revision: Union[str, None] = "002_vehicles"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "dt_edges",
        sa.Column(
            "is_roundabout",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )
    op.add_column(
        "dt_edges",
        sa.Column("roundabout_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        "idx_dt_edges_roundabout_id",
        "dt_edges",
        ["roundabout_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_dt_edges_roundabout_id", table_name="dt_edges")
    op.drop_column("dt_edges", "roundabout_id")
    op.drop_column("dt_edges", "is_roundabout")
