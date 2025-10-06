"""Wipe all old attendance and request data

Revision ID: bb7ac9b739f7
Revises: 58914c3b8f9e
Create Date: 2025-10-06 23:06:22.693381

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'bb7ac9b739f7'
down_revision = '58914c3b8f9e'
branch_labels = None
depends_on = None


def upgrade():
    # Delete all data from the attendance table
    op.execute("DELETE FROM attendance;")
    # Delete all data from the request table (if you have one)
    op.execute("DELETE FROM request;")
    # Delete all data from the guard_comment table
    op.execute("DELETE FROM guard_comment;")
    # NOTE: You MUST NOT delete users again, as the UPSERT logic handles them.


def downgrade():
    pass
