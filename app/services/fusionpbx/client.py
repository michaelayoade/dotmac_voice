"""FusionPBX REST API client."""

import httpx

from app.services.exceptions import BadRequestError, ServiceUnavailableError


class FusionpbxClient:
    """Client for FusionPBX REST API."""

    def __init__(self, base_url: str, api_key: str, timeout: float = 5.0) -> None:
        """Initialize FusionPBX client.

        Args:
            base_url: Base URL for FusionPBX API (e.g., 'http://fpbx')
            api_key: API key for authorization
            timeout: Request timeout in seconds
        """
        self._base = base_url.rstrip("/")
        self._client = httpx.Client(
            base_url=self._base,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )

    def _get(self, path: str) -> dict:
        """Make a GET request.

        Args:
            path: API endpoint path

        Returns:
            Parsed JSON response

        Raises:
            ServiceUnavailableError: On transport error
            BadRequestError: On 4xx response
        """
        try:
            r = self._client.get(path)
        except httpx.HTTPError as exc:
            raise ServiceUnavailableError(f"FusionPBX unreachable: {exc}") from exc
        if r.status_code >= 400:
            raise BadRequestError(f"FusionPBX {r.status_code}: {r.text}")
        return r.json()

    def _post(self, path: str, payload: dict) -> dict:
        """Make a POST request.

        Args:
            path: API endpoint path
            payload: Request payload

        Returns:
            Parsed JSON response

        Raises:
            ServiceUnavailableError: On transport error
            BadRequestError: On 4xx response
        """
        try:
            r = self._client.post(path, json=payload)
        except httpx.HTTPError as exc:
            raise ServiceUnavailableError(f"FusionPBX unreachable: {exc}") from exc
        if r.status_code >= 400:
            raise BadRequestError(f"FusionPBX {r.status_code}: {r.text}")
        return r.json()

    def list_domains(self) -> list[dict]:
        """List all domains.

        Returns:
            List of domain dictionaries

        Raises:
            ServiceUnavailableError: On transport error
            BadRequestError: On 4xx response
        """
        return self._get("/api/domains").get("domains", [])

    def create_domain(self, name: str) -> dict:
        """Create a new domain.

        Args:
            name: Domain name

        Returns:
            Created domain dictionary

        Raises:
            ServiceUnavailableError: On transport error
            BadRequestError: On 4xx response
        """
        return self._post("/api/domains", {"name": name})

    def list_extensions(self, domain: str) -> list[dict]:
        """List extensions for a domain.

        Args:
            domain: Domain name

        Returns:
            List of extension dictionaries

        Raises:
            ServiceUnavailableError: On transport error
            BadRequestError: On 4xx response
        """
        return self._get(f"/api/domains/{domain}/extensions").get("extensions", [])

    def create_extension(
        self, domain: str, number: str, password: str, display_name: str = ""
    ) -> dict:
        """Create a new extension for a domain.

        Args:
            domain: Domain name
            number: Extension number
            password: Extension password
            display_name: Display name (optional)

        Returns:
            Created extension dictionary

        Raises:
            ServiceUnavailableError: On transport error
            BadRequestError: On 4xx response
        """
        return self._post(
            f"/api/domains/{domain}/extensions",
            {
                "number": number,
                "password": password,
                "display_name": display_name,
            },
        )
