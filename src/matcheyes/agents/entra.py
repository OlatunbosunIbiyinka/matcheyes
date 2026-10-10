"""Microsoft Entra ID tokens for the optional model endpoint.

The only module that imports a cloud SDK (`azure-identity`, the optional `azure` extra). The SDK is
imported only when a token provider is built, i.e. only when MATCHEYES_LLM_AUTH=entra, so the
engine's runtime dependency stays pydantic. DefaultAzureCredential resolves `az login` locally or
a managed identity in Azure: no key exists to leak. The identity needs the "Cognitive Services
OpenAI User" role on the Foundry resource.
"""

import threading
import time
from collections.abc import Callable

from matcheyes.agents.llm import TokenProvider

SCOPE = "https://ai.azure.com/.default"
"""Token audience for Microsoft Foundry / Azure OpenAI v1 endpoints."""

REFRESH_MARGIN_S = 300.0
CLI_TIMEOUT_S = 60


class CachedToken:
    """A thread-safe bearer token: fetched by one caller at a time, reused until it nears expiry.

    Concurrent investigations must not each start a credential lookup (locally that spawns the
    Azure CLI per call, which times out under load)."""

    def __init__(
        self,
        fetch: Callable[[], tuple[str, float]],
        clock: Callable[[], float] = time.time,
        margin_s: float = REFRESH_MARGIN_S,
    ) -> None:
        """`fetch` returns (token, expiry as a Unix time)."""
        self._fetch = fetch
        self._clock = clock
        self._margin = margin_s
        self._lock = threading.Lock()
        self._token: str | None = None
        self._expires = 0.0

    def __call__(self) -> str:
        with self._lock:
            if self._token is None or self._clock() >= self._expires - self._margin:
                self._token, self._expires = self._fetch()
            return self._token


def token_provider() -> TokenProvider:
    """A callable returning a current bearer token from DefaultAzureCredential."""
    from azure.identity import DefaultAzureCredential

    credential = DefaultAzureCredential(process_timeout=CLI_TIMEOUT_S)

    def fetch() -> tuple[str, float]:
        token = credential.get_token(SCOPE)
        return token.token, float(token.expires_on)

    return CachedToken(fetch)
