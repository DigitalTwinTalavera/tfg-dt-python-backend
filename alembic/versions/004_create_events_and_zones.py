"""Create events and zones tables (Módulo 4 TFG)

Revision ID: 004_events_zones
Revises: 003_roundabout_edges
Create Date: 2026-04-23

Tablas:
  - dt_incidents: accidentes/obras/averías/eventos sobre una arista
  - dt_zones: polígonos de control (ZBE, restringidas, peatonales)
"""

from typing import Sequence, Union

import geoalchemy2
import sqlalchemy as sa
from alembic import op

revision: str = "004_events_zones"
down_revision: Union[str, None] = "003_roundabout_edges"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- dt_incidents ---
    op.create_table(
        "dt_incidents",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("type", sa.String(length=50), nullable=False),
        sa.Column("edge_id", sa.Integer(), nullable=False),
        sa.Column(
            "lanes_affected",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "start_time", sa.Float(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("duration_s", sa.Float(), nullable=True),
        sa.Column(
            "severity", sa.Integer(), nullable=False, server_default=sa.text("1")
        ),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["edge_id"], ["dt_edges.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_dt_incidents_status", "dt_incidents", ["status"], unique=False
    )
    op.create_index(
        "idx_dt_incidents_edge_id", "dt_incidents", ["edge_id"], unique=False
    )

    # --- dt_zones ---
    op.create_table(
        "dt_zones",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("zone_type", sa.String(length=50), nullable=False),
        sa.Column(
            "geometry",
            geoalchemy2.types.Geometry(
                geometry_type="POLYGON",
                srid=4326,
                from_text="ST_GeomFromEWKT",
                name="geometry",
            ),
            nullable=False,
        ),
        sa.Column(
            "restricted_vtypes",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("enforcement", sa.String(length=50), nullable=False),
        sa.Column(
            "active", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column("schedule_json", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_dt_zones_geometry_gist",
        "dt_zones",
        ["geometry"],
        unique=False,
        postgresql_using="gist",
    )
    op.create_index("idx_dt_zones_active", "dt_zones", ["active"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_dt_zones_active", table_name="dt_zones")
    op.drop_index(
        "idx_dt_zones_geometry_gist", table_name="dt_zones", postgresql_using="gist"
    )
    op.drop_table("dt_zones")

    op.drop_index("idx_dt_incidents_edge_id", table_name="dt_incidents")
    op.drop_index("idx_dt_incidents_status", table_name="dt_incidents")
    op.drop_table("dt_incidents")
