from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "vehicle_sources",
        sa.Column("odometer_manual", sa.Boolean(), nullable=False, server_default="0"),
    )


def downgrade():
    with op.batch_alter_table("vehicle_sources") as batch:
        batch.drop_column("odometer_manual")
