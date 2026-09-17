from geography.models import EconomicSystem, Economy

HIGH_INCOME = "High income"
WELFARE_SPENDING = 42.0
BORDERLINE_SPENDING = 38.0
MAX_PLAUSIBLE_SPENDING = 70.0


def civilian_share(government_spending: float | None, military_spending: float | None) -> float | None:
    if government_spending is None or government_spending > MAX_PLAUSIBLE_SPENDING:
        return None
    return round(government_spending - (military_spending or 0.0), 1)


def classify(
    income_group: str | None,
    government_spending: float | None,
    military_spending: float | None,
    communist: bool,
) -> Economy:
    def economy(system, disputed=False, alternative=None) -> Economy:
        return Economy(system, disputed, alternative, income_group, government_spending, military_spending)

    if communist:
        if government_spending is not None:
            return economy(EconomicSystem.SOCIALIST_MARKET, True, EconomicSystem.MARKET)
        return economy(EconomicSystem.PLANNED)

    civilian = civilian_share(government_spending, military_spending)
    if civilian is None:
        return economy(None)
    if income_group == HIGH_INCOME and civilian >= WELFARE_SPENDING:
        return economy(EconomicSystem.WELFARE)
    if income_group == HIGH_INCOME and civilian >= BORDERLINE_SPENDING:
        return economy(EconomicSystem.MARKET, True, EconomicSystem.WELFARE)
    return economy(EconomicSystem.MARKET)
