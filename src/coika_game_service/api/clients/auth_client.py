from collections.abc import Sequence
from uuid import UUID

import httpx

# The auth service rejects requests with more ids than this
MAX_IDS_PER_REQUEST = 100


class AuthClient:
    """
    HTTP client for the auth service. The auth service owns the player's identity (name, avatar);
    this service only asks for it.
    """

    def __init__(self, http: httpx.AsyncClient, players_url: str):
        """
        Initializes the client with a shared HTTP client and the URL of the players lookup endpoint.
        """
        self.http = http
        self.players_url = players_url

    async def get_player_names(self, player_ids: Sequence[UUID], token: str) -> dict[UUID, str]:
        """
        Asks the auth service for the names of several players, in batches of MAX_IDS_PER_REQUEST.
        The caller's own access token is forwarded. Unknown players are missing from the result.
        Raises httpx.HTTPError if the auth service cannot be reached or answers with an error.
        """
        names: dict[UUID, str] = {}

        for start in range(0, len(player_ids), MAX_IDS_PER_REQUEST):
            chunk = player_ids[start:start + MAX_IDS_PER_REQUEST]
            # POST only to carry the ids in the body (100 UUIDs in a URL are ~4 KB); it just reads
            response = await self.http.post(
                self.players_url,
                json={"ids": [str(player_id) for player_id in chunk]},
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            names.update({UUID(item["id"]): item["name"] for item in response.json()})

        return names
