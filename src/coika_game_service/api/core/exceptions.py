
class IdempotencyKeyReused(Exception):
    """The idempotency key was already used by the player for a different game mode."""

class InvalidTokenError(Exception):
    """The access token is not acceptable. Always maps to a 401."""

class GameModeNotFound(Exception):
    """The game mode is not in the catalog."""

class MatchNotFound(Exception):
    """The match does not exist or belongs to another player."""


class MatchNotOpen(Exception):
    """The match is not in progress (abandoned or rejected), so it accepts no score."""


class ScoreAlreadyExists(Exception):
    """The match already has a score and the new submission carries different figures."""

class NonDailyLeaderboardWithDate(Exception):
    """A date was provided for a non-daily leaderboard."""

class LeaderboardInvalidDate(Exception):
    """The date requested is invalid (e.g. in the future)."""

