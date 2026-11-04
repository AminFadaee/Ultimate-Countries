import os
import time

from geography.sources.wikidata import QueryCache, Wikidata


class Answering:
    def __init__(self):
        self.asked = 0

    def post(self, url, data, headers, timeout):
        self.asked += 1
        return type("Response", (), {"raise_for_status": lambda self: None,
                                     "json": lambda self: {"results": {"bindings": [{"x": {"value": "Q1"}}]}}})()


def test_a_query_is_asked_once_while_its_answer_is_fresh(tmp_path):
    session = Answering()
    wikidata = Wikidata(session, QueryCache(tmp_path, reuse=True))
    assert wikidata._select("SELECT ?x") == wikidata._select("SELECT ?x") == [{"x": "Q1"}]
    assert session.asked == 1


def test_an_old_answer_is_asked_again(tmp_path):
    session = Answering()
    cache = QueryCache(tmp_path, reuse=True)
    Wikidata(session, cache)._select("SELECT ?x")
    old = time.time() - cache.max_age_s - 60
    for path in tmp_path.iterdir():
        os.utime(path, (old, old))
    Wikidata(session, cache)._select("SELECT ?x")
    assert session.asked == 2


def test_a_full_run_writes_answers_but_never_reads_them(tmp_path):
    session = Answering()
    wikidata = Wikidata(session, QueryCache(tmp_path))
    wikidata._select("SELECT ?x")
    wikidata._select("SELECT ?x")
    assert session.asked == 2 and any(tmp_path.iterdir())
