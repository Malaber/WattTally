from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "users", sa.Column("is_approved", sa.Boolean(), nullable=False, server_default=sa.false())
    )
    op.execute(sa.text("UPDATE users SET is_approved = 1"))
    op.create_table(
        "enrollment_links",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("expires_at", sa.String(), nullable=False),
    )


def downgrade():
    op.drop_table("enrollment_links")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("is_approved")
