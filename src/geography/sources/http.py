import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

USER_AGENT = "geography-anki/0.1 (country flashcard dataset)"
TIMEOUT = 90
RETRY_STATUSES = (429, 500, 502, 503, 504)


def create_session(retries: int = 5) -> requests.Session:
    retry = Retry(
        total=retries,
        backoff_factor=2,
        status_forcelist=RETRY_STATUSES,
        allowed_methods=("GET", "HEAD", "POST"),
        respect_retry_after_header=True,
    )
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session
