from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    for name in ("vehicle_model", "driver_name", "driver_address", "report_vin"):
        op.add_column("vehicles", sa.Column(name, sa.String(), nullable=False, server_default=""))


def downgrade():
    with op.batch_alter_table("vehicles") as batch:
        for name in ("vehicle_model", "driver_name", "driver_address", "report_vin"):
            batch.drop_column(name)
