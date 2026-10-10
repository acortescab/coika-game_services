
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

class RateLimitBlock(Exception):
    """
    The player exceeded the request limit. `remaining` is what is left of the quota and
    `ttl` the seconds until the window resets (what the client must wait).
    """
    def __init__(self, reason: str, remaining: int, ttl: int):
        super().__init__(reason)
        self.remaining = remaining
        self.ttl = ttl
        
class InvalidScore(Exception):
    """The submitted score breaks an anti-cheat rule. `reason` says which one."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason

