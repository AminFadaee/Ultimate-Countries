from dataclasses import replace

from geography.models import Currency


def main_codes(currencies: list[Currency], alpha_2: str | None, iso_codes: set[str]) -> set[str]:
    official = [currency.code for currency in currencies if currency.code in iso_codes]
    own = [code for code in official if alpha_2 and code.startswith(alpha_2)]
    if len(own) == 1:
        return set(own)
    return set(official or [currency.code for currency in currencies])


def with_main(currencies: list[Currency], alpha_2: str | None, iso_codes: set[str]) -> list[Currency]:
    main = main_codes(currencies, alpha_2, iso_codes)
    marked = [replace(currency, main=currency.code in main) for currency in currencies]
    return sorted(marked, key=lambda currency: not currency.main)
