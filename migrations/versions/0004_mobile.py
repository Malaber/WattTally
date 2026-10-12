from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "auth_sessions", sa.Column("kind", sa.String(), nullable=False, server_default="browser")
    )
    op.create_table(
        "mobile_codes",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("challenge", sa.String(), nullable=False),
        sa.Column("expires_at", sa.String(), nullable=False),
    )


def downgrade():
    op.drop_table("mobile_codes")
    with op.batch_alter_table("auth_sessions") as batch:
        batch.drop_column("kind")
