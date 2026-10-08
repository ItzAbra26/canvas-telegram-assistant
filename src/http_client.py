import time
from typing import Any

import requests

from src.errors import APIError


class HTTPClient:
    """Reintenta solo lecturas. Nunca imprime URLs/cuerpos ni excepciones de requests."""

    def __init__(self, service: str, token: str, session: requests.Session | None = None):
        self.service = service
        self.session = session or requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {token}"})

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        attempts = 3 if method == "GET" else 1
        for attempt in range(attempts):
            try:
                response = self.session.request(
                    method, url, timeout=(10, 35), allow_redirects=False, **kwargs
                )
            except requests.RequestException:
                if attempt + 1 == attempts:
                    raise APIError(self.service) from None
                time.sleep(2**attempt)
                continue
            if response.status_code in {429, 500, 502, 503, 504} and attempt + 1 < attempts:
                try:
                    wait = float(response.headers.get("Retry-After", 2**attempt))
                except ValueError:
                    wait = 2**attempt
                if wait > 30:
                    raise APIError(self.service, response.status_code)
                time.sleep(max(1, wait))
                continue
            return response
        raise APIError(self.service)

    def json(self, method: str, url: str, **kwargs: Any) -> Any:
        response = self.request(method, url, **kwargs)
        if not 200 <= response.status_code < 300:
            raise APIError(self.service, response.status_code)
        try:
            return response.json()
        except ValueError:
            raise APIError(self.service) from None
