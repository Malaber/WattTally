from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("chargers", sa.Column("session_energy_entity", sa.String(), nullable=True))


def downgrade():
    with op.batch_alter_table("chargers") as batch:
        batch.drop_column("session_energy_entity")
