"""HU-06: server-side validation of the scores (anti-cheat), rule by rule."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from coika_game_service.api.core.exceptions import InvalidScore
from coika_game_service.api.db.models import MatchStatus, RejectReason
from coika_game_service.api.schemas.scores import CreateScoreRequest
from coika_game_service.api.services import score_service
from coika_game_service.api.services.score_service import ScoreService
from tests.unit.test_score_service import MATCH_ID, PLAYER, build, rules

NOW = datetime(2026, 10, 10, 12, 0, 0, tzinfo=UTC)
TIER_MINIMUMS = [0, 3, 9, 19, 34, 55, 83, 119, 164, 219, 285]  # GDD Â§3.2


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch):
    """The service measures the duration against 'now': freeze it so limits are exact."""

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    monkeypatch.setattr(score_service, "datetime", FrozenDatetime)


def match_lasting(seconds):
    return SimpleNamespace(started_at=NOW - timedelta(seconds=seconds))


def build_service(**kwargs):
    """The service of the other unit tests, with a match that started 60 s before NOW."""
    service, journal = build(**kwargs)
    service.match_repo.match.started_at = NOW - timedelta(seconds=60)
    return service, journal


def check(score=1500, pieces=120, tier=7, seconds=60, **mode_rules):
    payload = CreateScoreRequest(score=score, pieces_dropped=pieces, highest_tier=tier)
    service = ScoreService(None, None, None, None, None)
    return service.is_score_valid(payload, match_lasting(seconds), rules(**mode_rules))


def test_a_legitimate_score_is_valid():
    assert check() == (True, None)


# --- Duration: always from the server's started_at --------------------------------------

def test_a_match_longer_than_the_maximum_duration_is_rejected():
    assert check(seconds=3601, max_duration_s=3600) == (False, RejectReason.DURATION)


def test_a_match_of_exactly_the_maximum_duration_is_valid():
    valid, _ = check(score=1500, pieces=7200, seconds=3600, max_duration_s=3600)

    assert valid


def test_the_duration_is_measured_from_started_at_of_the_server():
    """Only the stored start counts: the body carries no duration the client could fake."""
    assert "duration" not in CreateScoreRequest.model_fields
    assert check(seconds=7200, max_duration_s=3600)[1] == RejectReason.DURATION


def test_a_match_shorter_than_the_minimum_duration_is_rejected():
    assert check(score=10, tier=0, pieces=0, seconds=4, min_duration_s=5) == (
        False, RejectReason.DURATION_MIN)


def test_a_match_of_exactly_the_minimum_duration_is_valid():
    assert check(score=10, tier=0, pieces=0, seconds=5, min_duration_s=5)[0]


# --- Score range -------------------------------------------------------------------------

def test_a_score_above_the_maximum_is_rejected():
    assert check(score=10_001, max_score=10_000) == (False, RejectReason.SCORE_MAX)


def test_a_score_equal_to_the_maximum_is_valid():
    valid, _ = check(score=3000, tier=7, seconds=100, max_score=3000)

    assert valid


def test_a_score_below_the_minimum_is_rejected():
    assert check(score=99, min_score=100) == (False, RejectReason.SCORE_MIN)


def test_a_score_equal_to_the_minimum_is_valid():
    valid, _ = check(score=119, tier=7, min_score=119)

    assert valid


# --- Pieces: pieces_dropped <= duration / min_interval + 1 ---------------------------------

def test_more_pieces_than_the_interval_allows_is_rejected():
    # 60 s at 500 ms between pieces: 120 pieces, +1 of margin = 121
    assert check(pieces=122, seconds=60) == (False, RejectReason.PIECES)


def test_pieces_at_the_limit_are_valid():
    assert check(pieces=120, seconds=60)[0]
    assert check(pieces=121, seconds=60)[0]


def test_the_piece_limit_follows_the_configured_interval():
    assert check(pieces=61, seconds=60, min_piece_interval_ms=1000)[0]
    assert check(pieces=62, seconds=60, min_piece_interval_ms=1000)[1] == RejectReason.PIECES


# --- Score per second --------------------------------------------------------------------

def test_a_pace_above_the_maximum_points_per_second_is_rejected():
    # 60 s * 20 pts/s = 1200
    assert check(score=1201, seconds=60, max_score_per_s=20) == (False, RejectReason.SCORE_RATE)


def test_a_pace_equal_to_the_maximum_is_valid():
    assert check(score=1200, seconds=60, max_score_per_s=20)[0]


def test_a_fractional_points_per_second_limit_is_respected():
    # Numeric(10,2) in the database: 12.50 pts/s * 60 s = 750
    assert check(score=750, tier=5, seconds=60, max_score_per_s=12.5)[0]
    assert check(score=751, tier=5, seconds=60, max_score_per_s=12.5)[1] == RejectReason.SCORE_RATE


# --- Minimum score of the highest tier (GDD: 0, 3, 9, 19, ... 285) --------------------------

@pytest.mark.parametrize("tier", range(11))
def test_the_minimum_score_of_each_tier_is_valid(tier):
    assert check(score=max(TIER_MINIMUMS[tier], 1), tier=tier, pieces=0)[0]


@pytest.mark.parametrize("tier", range(1, 11))
def test_one_point_below_the_minimum_of_the_tier_is_rejected(tier):
    result = check(score=TIER_MINIMUMS[tier] - 1, tier=tier, pieces=0, min_score=1)

    assert result == (False, RejectReason.SCORE_TIER)


def test_the_tier_minimums_are_configurable():
    assert check(score=500, tier=2, min_score_by_tier=[0, 100, 1000])[1] == RejectReason.SCORE_TIER
    assert check(score=1000, tier=2, min_score_by_tier=[0, 100, 1000])[0]


# --- What a rejection does ---------------------------------------------------------------

@pytest.mark.parametrize(
    ("payload", "mode_rules", "reason"),
    [
        (dict(score=10_001), dict(max_score=10_000), RejectReason.SCORE_MAX),
        (dict(score=99), dict(min_score=100), RejectReason.SCORE_MIN),
        (dict(pieces_dropped=500), {}, RejectReason.PIECES),
        (dict(score=5000), dict(max_score_per_s=20), RejectReason.SCORE_RATE),
        (dict(score=100, highest_tier=7), {}, RejectReason.SCORE_TIER),
    ],
    ids=["score-max", "score-min", "pieces", "score-rate", "score-tier"],
)
async def test_an_invalid_score_rejects_the_match_and_stores_nothing(payload, mode_rules, reason):
    service, journal = build_service()
    figures = dict(score=1500, pieces_dropped=120, highest_tier=7) | payload
    original = service.game_mode_repo.get_game_mode

    async def get_game_mode(game_mode_id):
        base = await original(game_mode_id)
        return SimpleNamespace(**(vars(base) | mode_rules))

    service.game_mode_repo.get_game_mode = get_game_mode

    with pytest.raises(InvalidScore):
        await service.create_score(PLAYER, MATCH_ID, CreateScoreRequest(**figures))

    match = service.match_repo.match
    assert match.status == MatchStatus.REJECTED
    assert match.rejection_reason == reason
    # No row in scores, no ranking update; the rejection itself is committed
    assert journal.names() == ["commit"]


async def test_a_valid_score_does_not_reject_the_match():
    service, _ = build_service()

    await service.create_score(PLAYER, MATCH_ID, CreateScoreRequest(
        score=1500, pieces_dropped=120, highest_tier=7))

    assert service.match_repo.match.status == MatchStatus.FINISHED
    assert service.match_repo.match.rejection_reason is None


async def test_a_rejected_match_cannot_be_submitted_again():
    from coika_game_service.api.core.exceptions import MatchNotOpen

    service, _ = build_service()
    impossible = CreateScoreRequest(score=1, pieces_dropped=0, highest_tier=10)
    with pytest.raises(InvalidScore):
        await service.create_score(PLAYER, MATCH_ID, impossible)

    with pytest.raises(MatchNotOpen):
        await service.create_score(PLAYER, MATCH_ID, CreateScoreRequest(
            score=1500, pieces_dropped=120, highest_tier=7))
