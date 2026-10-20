"""Tests for app.slugs – Human-readable slug generator (T1.4).

Verifies word list hygiene, generator determinism, uniqueness guarantees,
retry & exhaustion behavior, regex validation rules, and database integration.
"""

from __future__ import annotations

import os
import random
import re
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.slugs
from app.db import Base, make_engine, make_session_factory
from app.models import App, AppKind, User
from app.slugs import (
    ADJECTIVES,
    GENERATED_SLUG_PATTERN,
    NOUNS,
    RESERVED_SLUGS,
    SLUG_PATTERN,
    SlugGenerationError,
    generate_slug,
    generate_unique_slug,
    is_valid_slug,
    new_unique_slug,
    slug_is_taken,
)


@pytest.fixture()
def db_session(tmp_path: Path):
    """Create a temporary SQLite database with all tables and yield a clean session."""
    db_file = tmp_path / "slugs_test.db"
    engine = make_engine(f"sqlite:///{db_file}")
    Base.metadata.create_all(engine)
    session_factory = make_session_factory(engine)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


# ---------------------------------------------------------------------------
# 1 & 2: Word-List Hygiene & Length
# ---------------------------------------------------------------------------


def test_word_list_hygiene():
    """1. Both word lists have >= 100 words, strictly lowercase [a-z]{3,8}, no duplicates,
    share no words, and do not contain reserved slugs.
    """
    word_pattern = re.compile(r"^[a-z]{3,8}$")

    assert len(ADJECTIVES) >= 100, f"Expected >= 100 adjectives, got {len(ADJECTIVES)}"
    assert len(NOUNS) >= 100, f"Expected >= 100 nouns, got {len(NOUNS)}"

    # All match ^[a-z]{3,8}$
    for adj in ADJECTIVES:
        assert word_pattern.fullmatch(adj), (
            f"Adjective '{adj}' does not match ^[a-z]{{3,8}}$"
        )
    for noun in NOUNS:
        assert word_pattern.fullmatch(noun), (
            f"Noun '{noun}' does not match ^[a-z]{{3,8}}$"
        )

    # No duplicates within either list
    assert len(ADJECTIVES) == len(set(ADJECTIVES)), "Duplicate found in ADJECTIVES"
    assert len(NOUNS) == len(set(NOUNS)), "Duplicate found in NOUNS"

    # The two lists share no words
    intersection = set(ADJECTIVES).intersection(set(NOUNS))
    assert not intersection, (
        f"Words shared between adjectives and nouns: {intersection}"
    )

    # No word is in RESERVED_SLUGS
    adj_reserved = set(ADJECTIVES).intersection(RESERVED_SLUGS)
    assert not adj_reserved, f"Adjectives contain reserved slug: {adj_reserved}"
    noun_reserved = set(NOUNS).intersection(RESERVED_SLUGS)
    assert not noun_reserved, f"Nouns contain reserved slug: {noun_reserved}"


def test_slug_maximum_length_budget():
    """2. Longest adjective + longest noun + 4 digits + 2 hyphens <= 63 characters."""
    longest_adj = max(len(w) for w in ADJECTIVES)
    longest_noun = max(len(w) for w in NOUNS)
    max_possible_length = longest_adj + 1 + longest_noun + 1 + 4  # adj-noun-NNNN

    assert max_possible_length <= 63, (
        f"Max possible slug length ({max_possible_length}) exceeds DNS limit (63)"
    )


# ---------------------------------------------------------------------------
# 3, 4, 5: Generator Behavior, Determinism & Distribution
# ---------------------------------------------------------------------------


def test_generate_slug_format():
    """3. generate_slug matches GENERATED_SLUG_PATTERN, SLUG_PATTERN, is_valid_slug,
    and consists of 3 hyphen-separated parts.
    """
    slug = generate_slug()
    assert GENERATED_SLUG_PATTERN.fullmatch(slug) is not None
    assert SLUG_PATTERN.fullmatch(slug) is not None
    assert is_valid_slug(slug) is True

    parts = slug.split("-")
    assert len(parts) == 3, f"Expected 3 parts, got {parts}"
    assert parts[0] in ADJECTIVES
    assert parts[1] in NOUNS
    assert len(parts[2]) == 4
    assert 1000 <= int(parts[2]) <= 9999


def test_generate_slug_deterministic_with_seeded_rng():
    """4. With random.Random(1234) injected, two calls give identical results,
    and the number part is between 1000 and 9999.
    """
    slug1 = generate_slug(random.Random(1234))
    slug2 = generate_slug(random.Random(1234))
    assert slug1 == slug2

    number_part = int(slug1.split("-")[2])
    assert 1000 <= number_part <= 9999


def test_distribution_sanity():
    """5. Distribution sanity: 1,000 raw draws from the default generator contain
    at least 990 distinct values, and use at least 20 different adjectives and 20 nouns.
    """
    draws = [generate_slug() for _ in range(1000)]
    distinct_slugs = set(draws)
    adjectives_seen = {s.split("-")[0] for s in draws}
    nouns_seen = {s.split("-")[1] for s in draws}

    assert len(distinct_slugs) >= 990
    assert len(adjectives_seen) >= 20
    assert len(nouns_seen) >= 20


# ---------------------------------------------------------------------------
# 6, 7, 8: Uniqueness & Retry
# ---------------------------------------------------------------------------


def test_ten_thousand_slugs_uniqueness():
    """6. THE 10,000 TEST: generate 10,000 slugs with a set-backed collision check."""
    # With ~142 adjectives and ~136 nouns and 9,000 numbers, the total state space
    # is ~1.74 * 10^8 possible slugs.
    # By the birthday paradox, the probability of at least one collision occurring
    # within 10,000 purely random draws is 1 - exp(-10000^2 / (2 * 1.74 * 10^8)) ≈ 25%.
    # Therefore, raw random draws could collide at this volume, meaning the set-backed
    # collision check and retry loop in generate_unique_slug (not sheer luck) actively
    # guarantees that all 10,000 results are strictly distinct.
    seen: set[str] = set()

    for _ in range(10_000):
        slug = generate_unique_slug(lambda s: s in seen)
        seen.add(slug)

    assert len(seen) == 10_000
    for s in seen:
        assert re.fullmatch(r"^[a-z0-9-]+$", s) is not None
        assert SLUG_PATTERN.fullmatch(s) is not None
        assert len(s) <= 63


def test_retry_mechanism(monkeypatch: pytest.MonkeyPatch):
    """7. Retry works: monkeypatch generate_slug to return values from an iterator,
    with is_taken rejecting the first three. Result is the 4th, exactly 4 attempts.
    """
    candidates = ["a-b-1111", "a-b-2222", "a-b-3333", "c-d-4444"]
    iterator = iter(candidates)
    attempts = 0

    def mock_generate_slug(rng=None):  # noqa: ARG001
        nonlocal attempts
        attempts += 1
        return next(iterator)

    monkeypatch.setattr(app.slugs, "generate_slug", mock_generate_slug)

    taken = {"a-b-1111", "a-b-2222", "a-b-3333"}
    result = generate_unique_slug(lambda s: s in taken)

    assert result == "c-d-4444"
    assert attempts == 4


def test_exhaustion_raises_slug_generation_error():
    """8. Exhaustion: is_taken always True raises SlugGenerationError after exactly
    max_attempts calls (tested with max_attempts=3).
    """
    call_count = 0

    def always_taken(_slug: str) -> bool:
        nonlocal call_count
        call_count += 1
        return True

    with pytest.raises(SlugGenerationError, match=r"after 3 attempts"):
        generate_unique_slug(always_taken, max_attempts=3)

    assert call_count == 3


# ---------------------------------------------------------------------------
# 9: Validation
# ---------------------------------------------------------------------------


def test_is_valid_slug():
    """9. is_valid_slug returns True for valid slugs and False for invalid / reserved,
    never raising exceptions on odd or malformed input.
    """
    valid_cases = ["calm-otter-4821", "a", "a1"]
    for slug in valid_cases:
        assert is_valid_slug(slug) is True, f"Expected {slug!r} to be valid"

    invalid_cases = [
        "",
        "-a",
        "a-",
        "A-b",
        "a_b",
        "a.b",
        "a b",
        "a/b",
        "a\n",
        "ünï",
        "www",
        "api",
        "x" * 64,
    ]
    for slug in invalid_cases:
        assert is_valid_slug(slug) is False, f"Expected {slug!r} to be invalid"


# ---------------------------------------------------------------------------
# 10: Database Integration
# ---------------------------------------------------------------------------


def test_database_slug_taken_and_new_unique_slug(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    """10. slug_is_taken and new_unique_slug against temporary SQLite database.
    Verifies collision detection and that new_unique_slug writes nothing to DB.
    """
    user = User(email="owner@example.com")
    db_session.add(user)
    db_session.flush()

    app_row = App(
        owner_id=user.id,
        name="Existing App",
        slug="calm-otter-4821",
        kind=AppKind.static,
    )
    db_session.add(app_row)
    db_session.commit()

    # slug_is_taken checks
    assert slug_is_taken(db_session, "calm-otter-4821") is True
    assert slug_is_taken(db_session, "brave-falcon-5555") is False

    # Monkeypatch generate_slug to return collision first, then available slug
    candidate_iter = iter(["calm-otter-4821", "brave-falcon-5555"])
    monkeypatch.setattr(
        app.slugs, "generate_slug", lambda rng=None: next(candidate_iter)
    )  # noqa: ARG005

    apps_count_before = db_session.scalar(select(func.count(App.id)))
    slug = new_unique_slug(db_session)

    assert slug == "brave-falcon-5555"
    apps_count_after = db_session.scalar(select(func.count(App.id)))
    assert apps_count_after == apps_count_before


# ---------------------------------------------------------------------------
# 11: Side-Effect Freedom (Subprocess Isolation)
# ---------------------------------------------------------------------------


def test_import_has_no_side_effects():
    """11. Importing app.slugs in a fresh subprocess without SECRET_KEY succeeds,
    and app.config is not loaded into sys.modules.
    """
    env = os.environ.copy()
    env.pop("SECRET_KEY", None)

    code = (
        "import sys\n"
        "import app.slugs\n"
        "assert 'app.config' not in sys.modules, 'app.config was imported unexpectedly'\n"
        "assert 'app.db' not in sys.modules, 'app.db was imported unexpectedly'\n"
        "assert 'app.models' not in sys.modules, 'app.models was imported unexpectedly'\n"
    )

    result = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, (
        f"Subprocess failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
