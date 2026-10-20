"""Small HTTP helper with retries and a polite User-Agent."""

from __future__ import annotations

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

USER_AGENT = "market-risk-lakehouse/0.1 (personal research project)"


def session(total_retries: int = 5, backoff: float = 1.5) -> requests.Session:
    retry = Retry(
        total=total_retries,
        backoff_factor=backoff,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
    )
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s


def get_text(url: str, params: dict | None = None, timeout: int = 60) -> str:
    with session() as s:
        resp = s.get(url, params=params, timeout=timeout)
        resp.raise_for_status()
        return resp.text
