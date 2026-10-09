import uuid
from datetime import UTC, datetime, timedelta, timezone

import pytest

from coika_game_service.api.core.game_modes import (
    CLASSIC_GAME_MODE_ID,
    DAILY_GAME_MODE_ID,
    GAME_MODES,
    daily_seed,
    is_daily,
)

INT32_MAX = 2_147_483_647


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (datetime(2026, 10, 10, 0, 0, 0, tzinfo=UTC), 20261010),
        (datetime(2026, 10, 10, 12, 30, 0, tzinfo=UTC), 20261010),
        (datetime(2026, 10, 10, 23, 59, 59, 999_999, tzinfo=UTC), 20261010),
        (datetime(2026, 10, 11, 0, 0, 0, tzinfo=UTC), 20261011),
        (datetime(2026, 1, 5, 8, 0, 0, tzinfo=UTC), 20260105),
        (datetime(2028, 2, 29, 10, 0, 0, tzinfo=UTC), 20280229),
        (datetime(2026, 12, 31, 23, 59, 59, tzinfo=UTC), 20261231),
        (datetime(2027, 1, 1, 0, 0, 0, tzinfo=UTC), 20270101),
    ],
    ids=[
        "start-of-day", "midday", "last-microsecond", "next-day", "zero-padded",
        "leap-day", "last-second-of-year", "first-second-of-year",
    ],
)
def test_the_seed_is_the_utc_date_as_yyyymmdd(moment, expected):
    assert daily_seed(moment) == expected


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        # 01:00 on the 11th in UTC+2 is still 23:00 on the 10th in UTC
        (datetime(2026, 10, 11, 1, 0, 0, tzinfo=timezone(timedelta(hours=2))), 20261010),
        # 23:00 on the 10th in UTC-5 is already 04:00 on the 11th in UTC
        (datetime(2026, 10, 10, 23, 0, 0, tzinfo=timezone(timedelta(hours=-5))), 20261011),
        (datetime(2026, 10, 10, 12, 0, 0, tzinfo=timezone(timedelta(hours=14))), 20261009),
        (datetime(2026, 10, 10, 12, 0, 0, tzinfo=timezone(timedelta(hours=-12))), 20261011),
    ],
    ids=["east-of-utc", "west-of-utc", "far-east", "far-west"],
)
def test_the_date_is_always_the_utc_one_whatever_the_zone_of_the_moment(moment, expected):
    """Everybody gets the same number on the same UTC day, wherever the clock says it is."""
    assert daily_seed(moment) == expected


def test_the_same_instant_gives_the_same_seed_in_any_zone():
    instant = datetime(2026, 10, 10, 22, 30, 0, tzinfo=UTC)
    zones = [timezone(timedelta(hours=h)) for h in (-12, -5, 0, 2, 9, 14)]

    assert {daily_seed(instant.astimezone(zone)) for zone in zones} == {20261010}


def test_the_seed_is_an_int_that_fits_the_database_column_for_any_year():
    seed = daily_seed(datetime(9999, 12, 31, 23, 59, 59, tzinfo=UTC))

    assert isinstance(seed, int)
    assert seed == 99991231
    assert seed < INT32_MAX


def test_consecutive_days_give_different_increasing_seeds():
    first = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
    seeds = [daily_seed(first + timedelta(days=n)) for n in range(400)]

    assert len(set(seeds)) == 400
    assert seeds == sorted(seeds)


def test_only_the_daily_mode_is_daily():
    assert is_daily(DAILY_GAME_MODE_ID) is True
    assert is_daily(CLASSIC_GAME_MODE_ID) is False
    assert is_daily(uuid.uuid4()) is False


def test_the_catalog_has_classic_and_daily_with_distinct_ids_and_no_zen():
    assert GAME_MODES == {"classic": CLASSIC_GAME_MODE_ID, "daily": DAILY_GAME_MODE_ID}
    assert len(set(GAME_MODES.values())) == 2
    assert "zen" not in GAME_MODES


def test_the_fixed_ids_never_change():
    """These ids are written in migrations and used by clients: changing one breaks both."""
    assert str(CLASSIC_GAME_MODE_ID) == "af6e8f8c-cab7-4e4d-8ca5-5c564eed4728"
    assert str(DAILY_GAME_MODE_ID) == "299a2835-4881-4f0c-98f4-0fceda03acca"
