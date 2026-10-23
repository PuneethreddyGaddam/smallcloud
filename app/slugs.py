"""Human-readable, DNS-safe slug generation for Small Cloud applications.

This module generates readable subdomain slugs such as ``calm-otter-4821`` that
form an application's web address (e.g. ``calm-otter-4821.localhost:8000``).

Security & Architectural Notes:
    * **Slugs are identifiers, not secrets**: A slug serves as a routing label
      for the reverse proxy to identify which container to direct HTTP requests
      to. Access control and privacy come from authentication cookies, session
      validation, and owner scoping (introduced in Milestone M2), NOT from
      attempting to keep the slug secret.
    * **Zero Side-Effects**: Importing this module causes no settings reads,
      engine initialization, database connections, filesystem access, or network
      calls. ``app.models`` is only imported locally inside functions that query
      the database.
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import random

    from sqlalchemy.orm import Session

# ---------------------------------------------------------------------------
# Reserved Names & Regex Patterns
# ---------------------------------------------------------------------------

RESERVED_SLUGS: frozenset[str] = frozenset(
    {
        "admin",
        "api",
        "app",
        "assets",
        "auth",
        "dashboard",
        "docs",
        "health",
        "localhost",
        "login",
        "mail",
        "static",
        "status",
        "www",
    }
)
"""Subdomains reserved for platform infrastructure and routing endpoints."""

SLUG_PATTERN: re.Pattern[str] = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
"""Compiled regular expression for any valid DNS subdomain label (1 to 63 chars)."""

GENERATED_SLUG_PATTERN: re.Pattern[str] = re.compile(r"^[a-z]+-[a-z]+-[0-9]{4}$")
"""Compiled regular expression for slugs produced by this module."""

MAX_ATTEMPTS: int = 10
"""Maximum number of slug generation attempts before failing with SlugGenerationError."""

# ---------------------------------------------------------------------------
# Word Lists
# ---------------------------------------------------------------------------

ADJECTIVES: tuple[str, ...] = (
    "agile",
    "alert",
    "amber",
    "blithe",
    "bold",
    "brave",
    "breezy",
    "bright",
    "brisk",
    "buoyant",
    "calm",
    "candid",
    "careful",
    "caring",
    "clean",
    "clever",
    "cozy",
    "crisp",
    "dapper",
    "daring",
    "decent",
    "eager",
    "earnest",
    "easy",
    "elated",
    "exact",
    "fair",
    "famous",
    "fast",
    "fine",
    "fleet",
    "fond",
    "frank",
    "fresh",
    "frosty",
    "gallant",
    "genial",
    "gentle",
    "glossy",
    "glowing",
    "golden",
    "good",
    "grand",
    "grassy",
    "great",
    "green",
    "handy",
    "happy",
    "hardy",
    "healthy",
    "hearty",
    "honest",
    "hopeful",
    "humble",
    "jaunty",
    "jolly",
    "jovial",
    "joyful",
    "joyous",
    "keen",
    "kind",
    "kindly",
    "leafy",
    "light",
    "limber",
    "lively",
    "logical",
    "lovely",
    "loyal",
    "lucid",
    "lucky",
    "mellow",
    "merry",
    "mighty",
    "misty",
    "modest",
    "neat",
    "nimble",
    "noble",
    "patient",
    "peppy",
    "perky",
    "polite",
    "pretty",
    "prompt",
    "proud",
    "pure",
    "quick",
    "quiet",
    "radiant",
    "ready",
    "refined",
    "rich",
    "robust",
    "rosy",
    "rustic",
    "safe",
    "secure",
    "serene",
    "shining",
    "silent",
    "silver",
    "simple",
    "sincere",
    "sleek",
    "smart",
    "smooth",
    "snappy",
    "snowy",
    "snug",
    "sober",
    "sparky",
    "speedy",
    "spry",
    "stable",
    "starry",
    "steady",
    "stellar",
    "strong",
    "sturdy",
    "subtle",
    "sunny",
    "superb",
    "sure",
    "sweet",
    "swift",
    "tender",
    "thrifty",
    "tidy",
    "true",
    "trusty",
    "upbeat",
    "valid",
    "valiant",
    "vibrant",
    "vivid",
    "warm",
    "wealthy",
    "wise",
    "witty",
    "worthy",
    "zippy",
)
"""Family-friendly, positive adjectives formatted as lowercase 3-8 char words."""

NOUNS: tuple[str, ...] = (
    "acorn",
    "badger",
    "beacon",
    "beaver",
    "beetle",
    "bison",
    "blossom",
    "boulder",
    "breeze",
    "brook",
    "canyon",
    "cedar",
    "channel",
    "cliff",
    "clover",
    "comet",
    "compass",
    "coral",
    "cove",
    "crag",
    "crane",
    "creek",
    "cricket",
    "daisy",
    "delta",
    "dolphin",
    "dragon",
    "dune",
    "eagle",
    "falcon",
    "fawn",
    "feather",
    "fern",
    "finch",
    "fir",
    "firefly",
    "forest",
    "fox",
    "frog",
    "galaxy",
    "garden",
    "geyser",
    "glade",
    "glen",
    "grove",
    "gull",
    "harbor",
    "hare",
    "haven",
    "hawk",
    "heron",
    "hill",
    "hornet",
    "island",
    "jasper",
    "jay",
    "jungle",
    "kelp",
    "lagoon",
    "lake",
    "lark",
    "laurel",
    "leaf",
    "ledge",
    "leopard",
    "lily",
    "lynx",
    "mantis",
    "maple",
    "meadow",
    "meteor",
    "moose",
    "moss",
    "nebula",
    "nest",
    "oak",
    "oasis",
    "ocean",
    "orchard",
    "otter",
    "owl",
    "panda",
    "panther",
    "parrot",
    "path",
    "pebble",
    "pelican",
    "penguin",
    "pine",
    "planet",
    "plateau",
    "pond",
    "prairie",
    "quail",
    "quarry",
    "quartz",
    "rabbit",
    "radish",
    "raven",
    "reef",
    "ridge",
    "river",
    "robin",
    "sail",
    "salmon",
    "sapling",
    "savanna",
    "sea",
    "seal",
    "shadow",
    "shore",
    "sparrow",
    "spring",
    "spruce",
    "star",
    "stream",
    "summit",
    "sun",
    "swallow",
    "swan",
    "thicket",
    "tide",
    "timber",
    "trail",
    "tree",
    "tulip",
    "valley",
    "vine",
    "violet",
    "walnut",
    "wave",
    "willow",
    "wind",
    "wood",
    "wren",
    "zephyr",
)
"""Family-friendly, natural nouns formatted as lowercase 3-8 char words."""

# Cryptographically secure system random instance
_SYSTEM_RNG = secrets.SystemRandom()

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class SlugGenerationError(RuntimeError):
    """Raised when no unique slug could be generated within the attempt limit."""


# ---------------------------------------------------------------------------
# Functions
# ---------------------------------------------------------------------------


def is_valid_slug(slug: str) -> bool:
    """Return True if slug matches SLUG_PATTERN and is not in RESERVED_SLUGS.

    This function is completely safe and never raises exceptions for unexpected,
    empty, or malformed input types.
    """
    if not isinstance(slug, str):
        return False
    if not SLUG_PATTERN.fullmatch(slug):
        return False
    return slug not in RESERVED_SLUGS


def generate_slug(rng: random.Random | None = None) -> str:
    """Return a randomized slug in the format '{adjective}-{noun}-{number}'.

    Args:
        rng: Optional random number generator. Defaults to the cryptographically
            secure SystemRandom instance. Tests may supply a seeded Random
            instance for determinism.

    Returns:
        A three-part slug matching GENERATED_SLUG_PATTERN.
    """
    active_rng = rng if rng is not None else _SYSTEM_RNG
    adjective = active_rng.choice(ADJECTIVES)
    noun = active_rng.choice(NOUNS)
    number = active_rng.randrange(1000, 10000)
    return f"{adjective}-{noun}-{number}"


def generate_unique_slug(
    is_taken: Callable[[str], bool],
    *,
    max_attempts: int = MAX_ATTEMPTS,
    rng: random.Random | None = None,
) -> str:
    """Generate a slug that passes the provided is_taken collision check.

    Args:
        is_taken: Callable returning True if a slug is already allocated.
        max_attempts: Number of generation tries before raising an error.
        rng: Optional seeded random number generator.

    Returns:
        An available unique slug string.

    Raises:
        SlugGenerationError: If all max_attempts generate existing slugs.
    """
    for _ in range(max_attempts):
        # Resolve via module globals so tests can monkeypatch app.slugs.generate_slug
        try:
            slug = generate_slug(rng)
        except TypeError:
            slug = generate_slug()  # type: ignore[call-arg]
        if not is_taken(slug):
            return slug

    raise SlugGenerationError(
        f"Could not generate a unique slug after {max_attempts} attempts."
    )


def slug_is_taken(session: Session, slug: str) -> bool:
    """Return True if an App row with the specified slug already exists.

    Imports ``App`` locally to maintain zero side-effects at module load time.
    """
    from sqlalchemy import select

    from app.models import App

    statement = select(App.id).where(App.slug == slug).limit(1)
    return session.scalar(statement) is not None


def new_unique_slug(session: Session) -> str:
    """Generate a new unique slug checked against existing database apps.

    Does not commit or write any changes to the database.

    Note:
        The database UNIQUE constraint on ``apps.slug`` remains the final guard:
        if two concurrent requests race and an IntegrityError occurs on insert,
        the caller's transaction is rolled back and the deploy should be retried.
        Savepoints or nested transactions are intentionally avoided due to
        inconsistent behavior in SQLite drivers.

    Args:
        session: An active SQLAlchemy Session.

    Returns:
        A fresh, unused slug string.
    """
    return generate_unique_slug(lambda s: slug_is_taken(session, s))
