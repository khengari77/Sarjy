"""Daily Wordle puzzle, game and guess storage.

Puzzle rows are inserted as short-lived claims before the outbound fetch, so
``answer`` is nullable until a validated source response is stored.  The same
transactional claim columns back the exactly-one-fetch guarantee; no answer is
ever fabricated or copied from another date.
"""

import sqlalchemy as sa
from alembic import op

revision = "0011_wordle"
down_revision = "0010_context_retrieval_indexes"

TABLES = ("wordle_puzzles", "wordle_games", "wordle_guesses")


def upgrade():
    op.create_table(
        "wordle_puzzles",
        sa.Column("puzzle_date", sa.String(10), primary_key=True),
        sa.Column("answer", sa.String(5), nullable=True),
        sa.Column("source", sa.String(200), nullable=False),
        sa.Column("fetched_at", sa.String(40), nullable=True),
        sa.Column("payload_hash", sa.String(64), nullable=True),
        sa.Column("claim_id", sa.String(36), nullable=True),
        sa.Column("claim_expires_at", sa.String(40), nullable=True),
        sa.Column("retry_after", sa.String(40), nullable=True),
        sa.Column("last_attempt_at", sa.String(40), nullable=True),
    )

    op.create_table(
        "wordle_games",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(64), sa.ForeignKey("identities.id"), nullable=False),
        sa.Column("puzzle_date", sa.String(10), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("updated_at", sa.String(40), nullable=False),
        sa.UniqueConstraint("owner_id", "puzzle_date", name="uq_wordle_games_owner_date"),
    )
    op.create_index("ix_wordle_games_owner_status", "wordle_games", ["owner_id", "status"])
    op.create_index("ix_wordle_games_puzzle_date", "wordle_games", ["puzzle_date"])

    op.create_table(
        "wordle_guesses",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("game_id", sa.String(36), sa.ForeignKey("wordle_games.id"), nullable=False),
        sa.Column("owner_id", sa.String(64), sa.ForeignKey("identities.id"), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("word", sa.String(5), nullable=False),
        sa.Column("marks", sa.JSON(), nullable=False),
        sa.Column("request_id", sa.String(100), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.UniqueConstraint("game_id", "attempt", name="uq_wordle_guesses_game_attempt"),
        sa.UniqueConstraint("owner_id", "request_id", name="uq_wordle_guesses_owner_request"),
    )
    op.create_index("ix_wordle_guesses_game_id", "wordle_guesses", ["game_id"])
    op.create_index("ix_wordle_guesses_owner_id", "wordle_guesses", ["owner_id"])

    if op.get_bind().dialect.name == "postgresql":
        for table in TABLES:
            op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
            REVOKE ALL ON wordle_puzzles, wordle_games, wordle_guesses FROM anon;
        END IF;
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
            REVOKE ALL ON wordle_puzzles, wordle_games, wordle_guesses FROM authenticated;
        END IF;
        END $$""")


def downgrade():
    for table in reversed(TABLES):
        op.drop_table(table)
