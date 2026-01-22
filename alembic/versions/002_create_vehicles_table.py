"""Create vehicles table

Revision ID: 002_vehicles
Revises: 001_road_network
Create Date: 2024-01-22

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB
from geoalchemy2 import Geometry


# revision identifiers, used by Alembic.
revision: str = "002_vehicles"
down_revision: Union[str, None] = "001_road_network"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create dt_vehicles table with PostGIS geometry and indexes."""
    op.create_table(
        "dt_vehicles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "position",
            Geometry(geometry_type="POINT", srid=4326, spatial_index=False),
            nullable=False,
        ),
        sa.Column("velocity", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("acceleration", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("heading", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column(
            "status", sa.String(50), nullable=False, server_default="idle"
        ),
        sa.Column(
            "current_edge_id",
            sa.Integer(),
            sa.ForeignKey("dt_edges.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("route_edges", JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    # Create indexes
    op.create_index(
        "idx_dt_vehicles_position_gist",
        "dt_vehicles",
        ["position"],
        postgresql_using="gist",
    )
    op.create_index(
        "idx_dt_vehicles_status",
        "dt_vehicles",
        ["status"],
    )
    op.create_index(
        "idx_dt_vehicles_current_edge_id",
        "dt_vehicles",
        ["current_edge_id"],
    )
    op.create_index(
        "idx_dt_vehicles_updated_at",
        "dt_vehicles",
        ["updated_at"],
    )


def downgrade() -> None:
    """Drop dt_vehicles table and indexes."""
    op.drop_index("idx_dt_vehicles_updated_at", table_name="dt_vehicles")
    op.drop_index("idx_dt_vehicles_current_edge_id", table_name="dt_vehicles")
    op.drop_index("idx_dt_vehicles_status", table_name="dt_vehicles")
    op.drop_index("idx_dt_vehicles_position_gist", table_name="dt_vehicles")
    op.drop_table("dt_vehicles")
