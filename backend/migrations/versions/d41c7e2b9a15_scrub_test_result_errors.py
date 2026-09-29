"""scrub stored test-result error text

The test-run worker stored the tail of each failing question's exception in
test_results.error and returned it to every project viewer — provider error
bodies, request URLs, model names and workspace paths included (R2-07). The
worker now stores a fixed message and keeps the exception in the server log;
this migration applies the same message to the rows written before it, so
the history stops leaking too. Data only, no schema change.

Revision ID: d41c7e2b9a15
Revises: b7e2c4a91d30
"""

from alembic import op

revision = "d41c7e2b9a15"
down_revision = "b7e2c4a91d30"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Literal of services.test_runs.QUESTION_FAILED_ERROR, frozen here: a
    # migration must not import application code that later changes.
    op.execute("UPDATE test_results SET error = 'query failed' WHERE error IS NOT NULL")


def downgrade() -> None:
    # The original text is gone on purpose; nothing to restore.
    pass
