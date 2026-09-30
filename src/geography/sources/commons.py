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
MAX_CREDIT = 90
TAGS = re.compile(r"<[^>]+>")
SPACES = re.compile(r"\s+")
FILE_PREFIX = re.compile(r"\S+\.(?:jpe?g|png|tiff?|svg)\s*:\s*", re.IGNORECASE)
TALK = re.compile(r"\(\s*talk\s*\)", re.IGNORECASE)
DERIVATIVE = re.compile(r"\s*derivative work:\s*", re.IGNORECASE)


@dataclass(frozen=True)
class CommonsImage:
    thumbnail_url: str
    author: str
    license: str

    @property
    def credit(self) -> str:
        return f"Photo: {self.author} · {self.license} · Wikimedia Commons"


def plain(markup: str) -> str:
    return SPACES.sub(" ", TAGS.sub("", markup)).strip()


def tidy_author(author: str) -> str:
    text = DERIVATIVE.sub(", ", TALK.sub("", FILE_PREFIX.sub("", author)))
    text = SPACES.sub(" ", text).strip(" ,;")
    if len(text) > MAX_CREDIT:
        text = text[:MAX_CREDIT].rsplit(" ", 1)[0].rstrip(" ,;") + "…"
    if text.count("(") != text.count(")"):
        text = SPACES.sub(" ", text.replace("(", "").replace(")", "")).strip(" ,;")
    return text or "Unknown author"


def tidy_credit(credit: str) -> str:
    prefix, _, rest = credit.partition("Photo: ")
    author, separator, tail = rest.partition(" · ")
    return f"{prefix}Photo: {tidy_author(author)}{separator}{tail}"


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


def describe_image(session: requests.Session, image_url: str) -> CommonsImage:
    params = {"action": "query", "titles": file_title(image_url), "prop": "imageinfo",
              "iiprop": "url|extmetadata", "iiurlwidth": THUMB_WIDTH, "format": "json"}
    page = next(iter(get_with_retries(session, API_URL, params=params).json()["query"]["pages"].values()))
    info = page["imageinfo"][0]
    metadata = info["extmetadata"]
    author = tidy_author(plain(metadata.get("Artist", {}).get("value", "")))
    license_name = metadata.get("LicenseShortName", {}).get("value", "")
    return CommonsImage(info["thumburl"], author, license_name)


def download_thumbnail(session: requests.Session, image: CommonsImage) -> bytes:
    content = get_with_retries(session, image.thumbnail_url).content
    time.sleep(PAUSE_SECONDS)
    return content
