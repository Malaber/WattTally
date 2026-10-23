from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("vehicle_sources", sa.Column("odometer_entity", sa.String(), nullable=True))


def downgrade():
    with op.batch_alter_table("vehicle_sources") as batch:
        batch.drop_column("odometer_entity")
