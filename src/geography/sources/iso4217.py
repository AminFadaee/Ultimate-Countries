import requests
from lxml import etree

from geography.sources.http import TIMEOUT

CURRENCIES_URL = "https://www.six-group.com/dam/download/financial-information/data-center/iso-currrency/lists/list-one.xml"


def fetch_currency_codes(session: requests.Session) -> set[str]:
    response = session.get(CURRENCIES_URL, timeout=TIMEOUT)
    response.raise_for_status()
    document = etree.fromstring(response.content)
    return {code.text for code in document.iter("Ccy") if code.text}
