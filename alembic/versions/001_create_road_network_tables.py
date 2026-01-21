"""Create road network tables (dt_nodes and dt_edges)

Revision ID: 001_road_network
Revises:
Create Date: 2024-01-18

"""

from typing import Sequence, Union

import geoalchemy2
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "001_road_network"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create dt_nodes table (prefixed to avoid conflicts with PostGIS Tiger tables)
    op.create_table(
        "dt_nodes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("node_type", sa.String(length=50), nullable=False),
        sa.Column(
            "position",
            geoalchemy2.types.Geometry(
                geometry_type="POINT",
                srid=4326,
                from_text="ST_GeomFromEWKT",
                name="geometry",
            ),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("metadata_json", sa.String(), nullable=True),
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

    # Create indexes for dt_nodes table
    op.create_index(
        "idx_dt_nodes_position_gist",
        "dt_nodes",
        ["position"],
        unique=False,
        postgresql_using="gist",
    )
    op.create_index("idx_dt_nodes_node_type", "dt_nodes", ["node_type"], unique=False)
    op.create_index("idx_dt_nodes_is_active", "dt_nodes", ["is_active"], unique=False)

    # Create dt_edges table (prefixed to avoid conflicts with PostGIS Tiger tables)
    op.create_table(
        "dt_edges",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("start_node_id", sa.Integer(), nullable=False),
        sa.Column("end_node_id", sa.Integer(), nullable=False),
        sa.Column("road_type", sa.String(length=50), nullable=False),
        sa.Column(
            "geometry",
            geoalchemy2.types.Geometry(
                geometry_type="LINESTRING",
                srid=4326,
                from_text="ST_GeomFromEWKT",
                name="geometry",
            ),
            nullable=False,
        ),
        sa.Column("length", sa.Float(), nullable=False),
        sa.Column("max_speed", sa.Integer(), nullable=False),
        sa.Column("lanes", sa.Integer(), nullable=False),
        sa.Column("one_way", sa.Boolean(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("metadata_json", sa.String(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["start_node_id"], ["dt_nodes.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["end_node_id"], ["dt_nodes.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # Create indexes for dt_edges table
    op.create_index(
        "idx_dt_edges_geometry_gist",
        "dt_edges",
        ["geometry"],
        unique=False,
        postgresql_using="gist",
    )
    op.create_index("idx_dt_edges_road_type", "dt_edges", ["road_type"], unique=False)
    op.create_index(
        "idx_dt_edges_start_node_id", "dt_edges", ["start_node_id"], unique=False
    )
    op.create_index(
        "idx_dt_edges_end_node_id", "dt_edges", ["end_node_id"], unique=False
    )
    op.create_index("idx_dt_edges_is_active", "dt_edges", ["is_active"], unique=False)


def downgrade() -> None:
    # Drop indexes for dt_edges
    op.drop_index("idx_dt_edges_is_active", table_name="dt_edges")
    op.drop_index("idx_dt_edges_end_node_id", table_name="dt_edges")
    op.drop_index("idx_dt_edges_start_node_id", table_name="dt_edges")
    op.drop_index("idx_dt_edges_road_type", table_name="dt_edges")
    op.drop_index("idx_dt_edges_geometry_gist", table_name="dt_edges", postgresql_using="gist")

    # Drop dt_edges table
    op.drop_table("dt_edges")

    # Drop indexes for dt_nodes
    op.drop_index("idx_dt_nodes_is_active", table_name="dt_nodes")
    op.drop_index("idx_dt_nodes_node_type", table_name="dt_nodes")
    op.drop_index("idx_dt_nodes_position_gist", table_name="dt_nodes", postgresql_using="gist")

    # Drop dt_nodes table
    op.drop_table("dt_nodes")
