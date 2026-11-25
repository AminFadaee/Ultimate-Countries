import os

from geography import data


class Response:
    def __init__(self, chunks):
        self.chunks = chunks

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size):
        yield from self.chunks()


def test_two_workers_downloading_the_same_file_both_succeed(tmp_path, monkeypatch):
    target = tmp_path / "zones.zip"
    pids = iter([101, 202])
    monkeypatch.setattr(os, "getpid", lambda: next(pids))

    def other_worker_finishes_first():
        yield b"first"
        data.download_file("https://example.org/zones.zip", target)
        yield b" half"

    responses = iter([Response(other_worker_finishes_first), Response(lambda: iter([b"whole"]))])
    monkeypatch.setattr(data.requests, "get", lambda url, stream, timeout: next(responses))

    assert data.download_file("https://example.org/zones.zip", target) == target
    assert target.exists()
