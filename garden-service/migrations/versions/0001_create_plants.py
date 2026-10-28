"""Create the persistent plants table."""

from alembic import op
import sqlalchemy as sa


revision = "0001_create_plants"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "plants",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("owner_id", sa.String(100), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("lat", sa.Numeric(8, 6), nullable=False),
        sa.Column("lon", sa.Numeric(9, 6), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("lat BETWEEN -90 AND 90", name="ck_plants_lat_bounds"),
        sa.CheckConstraint("lon BETWEEN -180 AND 180", name="ck_plants_lon_bounds"),
        sa.CheckConstraint(
            "char_length(name) BETWEEN 1 AND 100 AND name = btrim(name) "
            "AND name ~ '[^[:space:]]'", name="ck_plants_name",
        ),
    )
    op.create_index("ix_plants_owner_id", "plants", ["owner_id"])


def downgrade():
    op.drop_table("plants")
