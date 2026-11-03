import hashlib
import json
import pathlib
import re

import requests

RECORDED_HOSTS = ("wikidata.org", "wikipedia.org", "nominatim.openstreetmap.org")
VALUES = re.compile(r"VALUES (\?\w+) \{([^}]*)\}")
LISTS = {"osm_ids": ",", "ids": "|", "titles": "|"}


def canonical(value):
    if not isinstance(value, dict):
        return value
    found = {}
    for name, entry in value.items():
        if name == "query" and isinstance(entry, str):
            entry = VALUES.sub(lambda match: f"VALUES {match.group(1)} {{ {' '.join(sorted(match.group(2).split()))} }}", " ".join(entry.split()))
        elif name in LISTS and isinstance(entry, str):
            entry = LISTS[name].join(sorted(entry.split(LISTS[name])))
        found[name] = entry
    return sorted(found.items())


class MissingRecording(LookupError):
    pass


class Cassette:
    def __init__(self, path: pathlib.Path, record: bool):
        self.path = path
        self.record = record
        self.entries = json.loads(path.read_text()) if path.exists() else {}
        self.changed = False
        self.real_request = requests.Session.request

    def request(self, session: requests.Session, method: str, url: str, **kwargs) -> requests.Response:
        if not any(host in url for host in RECORDED_HOSTS):
            return self.real_request(session, method, url, **kwargs)
        key = self.key(method, url, kwargs.get("params"), kwargs.get("data"))
        if key not in self.entries:
            if not self.record:
                raise MissingRecording(f"No recorded response for {method} {url}; run pytest --record")
            response = self.real_request(session, method, url, **kwargs)
            self.entries[key] = {"status": response.status_code, "url": response.url, "body": response.text}
            self.changed = True
        return self.replay(self.entries[key])

    def save(self) -> None:
        if self.changed:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.entries, ensure_ascii=False, indent=0, sort_keys=True) + "\n")

    @staticmethod
    def key(method: str, url: str, params, data) -> str:
        text = json.dumps([method.upper(), url, canonical(params), canonical(data)], ensure_ascii=False, default=str)
        return hashlib.sha1(text.encode()).hexdigest()

    @staticmethod
    def replay(entry: dict) -> requests.Response:
        response = requests.Response()
        response.status_code = entry["status"]
        response.url = entry["url"]
        response._content = entry["body"].encode()
        response.encoding = "utf-8"
        response.headers["Content-Type"] = "application/json"
        return response
