import pathlib

import requests

from geography.sources.http import TIMEOUT


def download_flag(session: requests.Session, url: str, path: pathlib.Path) -> bool:
    response = session.get(url, timeout=TIMEOUT)
    response.raise_for_status()
    if path.exists() and path.read_bytes() == response.content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(response.content)
    return True
