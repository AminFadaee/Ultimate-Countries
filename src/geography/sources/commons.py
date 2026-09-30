import time
import urllib.parse

import requests

from geography.sources.http import TIMEOUT

API_URL = "https://commons.wikimedia.org/w/api.php"
THUMB_WIDTH = 640
ATTEMPTS = 5
BACKOFF_SECONDS = 10
PAUSE_SECONDS = 2


def file_title(image_url: str) -> str:
    return "File:" + urllib.parse.unquote(image_url.rsplit("/", 1)[-1])


def get_with_retries(session: requests.Session, url: str, **kwargs) -> requests.Response:
    for attempt in range(1, ATTEMPTS + 1):
        response = session.get(url, timeout=TIMEOUT, **kwargs)
        if response.status_code != 429:
            response.raise_for_status()
            return response
        time.sleep(BACKOFF_SECONDS * attempt)
    response.raise_for_status()
    return response


def thumbnail_url(session: requests.Session, image_url: str) -> str:
    params = {"action": "query", "titles": file_title(image_url), "prop": "imageinfo",
              "iiprop": "url", "iiurlwidth": THUMB_WIDTH, "format": "json"}
    page = next(iter(get_with_retries(session, API_URL, params=params).json()["query"]["pages"].values()))
    return page["imageinfo"][0]["thumburl"]


def download_thumbnail(session: requests.Session, url: str) -> bytes:
    content = get_with_retries(session, url).content
    time.sleep(PAUSE_SECONDS)
    return content
