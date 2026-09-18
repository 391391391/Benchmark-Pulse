"""Human-readable names for the codes the interface displays.

Tickers, ISO country codes and currency codes are how the data arrives and how
an analyst thinks, but a portfolio screen is also read by people who do not
spend their day in them. Rather than choosing between the two, the short form is
shown with the long form one hover away.

Kept separate from benchmarks.py because this is reference data with no
behaviour: nothing here affects a calculation.
"""

from __future__ import annotations

COUNTRY_NAMES: dict[str, str] = {
    "US": "United States",
    "IN": "India",
    "SA": "Saudi Arabia",
    "GB": "United Kingdom",
    "JP": "Japan",
    "NL": "Netherlands",
    "DK": "Denmark",
    "DE": "Germany",
    "FR": "France",
    "CH": "Switzerland",
    "SE": "Sweden",
    "NO": "Norway",
    "FI": "Finland",
    "IE": "Ireland",
    "BE": "Belgium",
    "AT": "Austria",
    "IT": "Italy",
    "ES": "Spain",
    "CA": "Canada",
    "AU": "Australia",
    "NZ": "New Zealand",
    "SG": "Singapore",
    "HK": "Hong Kong",
    "KR": "South Korea",
    "TW": "Taiwan",
    "CN": "China",
    "BR": "Brazil",
    "MX": "Mexico",
    "ZA": "South Africa",
    "AE": "United Arab Emirates",
    "QA": "Qatar",
    "KW": "Kuwait",
    "TR": "Turkey",
    "ID": "Indonesia",
    "TH": "Thailand",
    "MY": "Malaysia",
    "PH": "Philippines",
    "PL": "Poland",
    "CL": "Chile",
    "GR": "Greece",
    "EG": "Egypt",
    "WW": "Global",
}

CURRENCY_NAMES: dict[str, str] = {
    "USD": "US Dollar",
    "INR": "Indian Rupee",
    "SAR": "Saudi Riyal",
    "EUR": "Euro",
    "GBP": "Pound Sterling",
    "JPY": "Japanese Yen",
    "CHF": "Swiss Franc",
    "AED": "UAE Dirham",
    "QAR": "Qatari Riyal",
    "KWD": "Kuwaiti Dinar",
    "SGD": "Singapore Dollar",
    "HKD": "Hong Kong Dollar",
    "CNY": "Chinese Yuan",
    "AUD": "Australian Dollar",
    "CAD": "Canadian Dollar",
    "SEK": "Swedish Krona",
    "DKK": "Danish Krone",
    "NOK": "Norwegian Krone",
    "ZAR": "South African Rand",
    "BRL": "Brazilian Real",
    "KRW": "South Korean Won",
    "TWD": "Taiwan Dollar",
}

#: Measures whose names are precise but not self-explanatory. Shown on hover
#: beside the column heading rather than as a paragraph under it.
MEASURE_NOTES: dict[str, str] = {
    "IRR": "Internal rate of return, solved as XIRR over the actual cashflow "
           "dates: the annualised money-weighted return, accounting for when "
           "capital went in and came out.",
    "Bmk IRR": "The return the benchmark produced over the same cashflow "
               "pattern and the same dates.",
    "Direct Alpha": "Annualised return in excess of the benchmark. Computed "
                    "from the holding's own cashflows re-based to the "
                    "benchmark's growth, so it is comparable across holdings.",
    "Alpha vs sector": "Annualised return above the holding's sector index.",
    "Alpha vs mandate": "Annualised return of the whole portfolio above the "
                        "benchmark implied by its mandate.",
    "KS-PME": "Kaplan-Schoar Public Market Equivalent. Above 1.00 means the "
              "holding beat what the same money would have earned in the "
              "benchmark over the same dates.",
    "Total return": "Simple return over the whole holding period, not "
                    "annualised: what the position actually made.",
    "Trend": "Relative performance over six months, one year and three years, "
             "weighted 20/30/50. Measured on the security itself, so it does "
             "not depend on when the position was opened or what was paid.",
    "Weighted alpha": "Six-month, one-year and three-year alpha against the "
                      "same benchmark, combined 20/30/50 so the three-year "
                      "window decides half the score.",
    "6M alpha": "The security's return less its benchmark's over the last six "
                "months, measured on the same trading days. Point to point on "
                "the security, not on your money.",
    "1Y alpha": "The security's return less its benchmark's over the last "
                "twelve months.",
    "3Y alpha": "The security's return less its benchmark's over the last "
                "three years, annualised on both sides. The heaviest of the "
                "three windows.",
    "Weight": "Share of the portfolio by current market value.",
    "Avg cost": "Average purchase price per share, in the local currency.",
    "Last": "Most recent closing price, in the local currency.",
}


def country_name(code: str | None) -> str:
    if not code:
        return "Unknown"
    return COUNTRY_NAMES.get(code.strip().upper(), code.strip().upper())


def currency_name(code: str | None) -> str:
    if not code:
        return "Unknown"
    return CURRENCY_NAMES.get(code.strip().upper(), code.strip().upper())
