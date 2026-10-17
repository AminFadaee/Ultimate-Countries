import re
import time
import urllib.parse
from dataclasses import dataclass

import requests

from geography.sources.http import TIMEOUT

API_URL = "https://commons.wikimedia.org/w/api.php"
THUMB_WIDTH = 640
ATTEMPTS = 5
BACKOFF_SECONDS = 10
PAUSE_SECONDS = 2
NOT_A_PHOTO = re.compile(
    r"\b(maps?|diagrams?|locator|schematics?|satellite|charts?|plans?|drawings?|illustrations?|paintings?|engravings?|lithographs?|posters?)\b",
    re.IGNORECASE,
)
TAGS = re.compile(r"<[^>]+>")
SPACES = re.compile(r"\s+")


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


@dataclass(frozen=True)
class CommonsImage:
    thumbnail_url: str
    page: str
    author: str
    license: str
    license_url: str


def plain(markup: str) -> str:
    return SPACES.sub(" ", TAGS.sub("", markup)).strip()


def describe_image(session: requests.Session, image_url: str) -> CommonsImage | None:
    params = {"action": "query", "titles": file_title(image_url), "prop": "imageinfo|categories", "iiprop": "url|extmetadata",
              "iiurlwidth": THUMB_WIDTH, "cllimit": "max", "format": "json"}
    page = next(iter(get_with_retries(session, API_URL, params=params).json()["query"]["pages"].values()))
    categories = " ".join(category["title"] for category in page.get("categories", []))
    if NOT_A_PHOTO.search(f"{page['title']} {categories}"):
        return None
    info = page["imageinfo"][0]
    metadata = info["extmetadata"]

    def field(name: str) -> str:
        return plain(metadata.get(name, {}).get("value", ""))

    return CommonsImage(info["thumburl"], info["descriptionurl"], field("Artist") or field("Attribution"), field("LicenseShortName"), field("LicenseUrl"))


def download_thumbnail(session: requests.Session, url: str) -> bytes:
    content = get_with_retries(session, url).content
    time.sleep(PAUSE_SECONDS)
    return content
