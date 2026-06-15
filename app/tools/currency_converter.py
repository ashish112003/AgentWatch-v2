"""
app/tools/currency_converter.py
────────────────────────────────
LangChain Currency Converter Tool.

Converts amounts between common currencies using static exchange rates
stored in this file.  No external API calls are made.

Static rate design:
  All rates are expressed relative to USD as the base currency (1 USD = X).
  To convert A → B: amount_in_B = amount_in_A / rate_A * rate_B.
  Rates are approximate mid-market values typical of early 2024 and are
  suitable for educational and demonstration purposes.

  In a production system you would replace _RATES with a cached call to
  an exchange rate API (e.g. Open Exchange Rates, Frankfurter) refreshed
  on a schedule.

Supported currencies (ISO 4217 codes):
  USD, EUR, GBP, JPY, CAD, AUD, CHF, CNY, INR, MXN,
  BRL, KRW, SGD, HKD, NOK, SEK, DKK, NZD, ZAR, AED

Input format accepted:
  "100 USD to EUR"
  "50 GBP to INR"
  "1000 JPY to USD"
"""

from langchain_core.tools import tool


# ── Exchange rates relative to 1 USD ────────────────────────────────────────
# Source: approximate mid-market rates, early 2024.
# Update this dict when deploying to production.
_RATES: dict[str, float] = {
    "USD": 1.000000,
    "EUR": 0.921000,
    "GBP": 0.787000,
    "JPY": 149.50000,
    "CAD": 1.360000,
    "AUD": 1.530000,
    "CHF": 0.878000,
    "CNY": 7.240000,
    "INR": 83.12000,
    "MXN": 17.15000,
    "BRL": 4.970000,
    "KRW": 1325.0000,
    "SGD": 1.340000,
    "HKD": 7.820000,
    "NOK": 10.56000,
    "SEK": 10.42000,
    "DKK": 6.880000,
    "NZD": 1.630000,
    "ZAR": 18.63000,
    "AED": 3.673000,
}

# Human-readable currency names for richer output
_NAMES: dict[str, str] = {
    "USD": "US Dollar",
    "EUR": "Euro",
    "GBP": "British Pound",
    "JPY": "Japanese Yen",
    "CAD": "Canadian Dollar",
    "AUD": "Australian Dollar",
    "CHF": "Swiss Franc",
    "CNY": "Chinese Yuan",
    "INR": "Indian Rupee",
    "MXN": "Mexican Peso",
    "BRL": "Brazilian Real",
    "KRW": "South Korean Won",
    "SGD": "Singapore Dollar",
    "HKD": "Hong Kong Dollar",
    "NOK": "Norwegian Krone",
    "SEK": "Swedish Krona",
    "DKK": "Danish Krone",
    "NZD": "New Zealand Dollar",
    "ZAR": "South African Rand",
    "AED": "UAE Dirham",
}


def _convert(amount: float, from_currency: str, to_currency: str) -> str:
    """
    Perform the currency conversion and format the result.

    Conversion formula (USD as pivot):
        result = amount * (to_rate / from_rate)

    Args:
        amount:        The numeric amount to convert.
        from_currency: Source currency ISO code (upper-case).
        to_currency:   Target currency ISO code (upper-case).

    Returns:
        A formatted conversion result string.

    Raises:
        ValueError: Unknown currency code or invalid amount.
    """
    from_currency = from_currency.upper().strip()
    to_currency   = to_currency.upper().strip()

    if from_currency not in _RATES:
        supported = ", ".join(sorted(_RATES.keys()))
        raise ValueError(
            f"Unknown currency '{from_currency}'. "
            f"Supported: {supported}"
        )
    if to_currency not in _RATES:
        supported = ", ".join(sorted(_RATES.keys()))
        raise ValueError(
            f"Unknown currency '{to_currency}'. "
            f"Supported: {supported}"
        )
    if amount < 0:
        raise ValueError("Amount cannot be negative.")

    result = amount * (_RATES[to_currency] / _RATES[from_currency])

    # Format: no decimals for whole-number high-value currencies like JPY, KRW
    if to_currency in ("JPY", "KRW", "IDR"):
        result_str = f"{result:,.0f}"
    else:
        result_str = f"{result:,.4f}".rstrip("0").rstrip(".")

    from_name = _NAMES.get(from_currency, from_currency)
    to_name   = _NAMES.get(to_currency,   to_currency)

    # Format the input amount cleanly too
    if from_currency in ("JPY", "KRW"):
        amount_str = f"{amount:,.0f}"
    else:
        amount_str = f"{amount:,.2f}".rstrip("0").rstrip(".")

    return (
        f"{amount_str} {from_currency} ({from_name}) = "
        f"{result_str} {to_currency} ({to_name})\n"
        f"Rate: 1 {from_currency} = "
        f"{(_RATES[to_currency] / _RATES[from_currency]):.6f} {to_currency}\n"
        f"(Rates are static approximations for educational use.)"
    )


def _parse_query(query: str) -> tuple[float, str, str]:
    """
    Parse a natural-language conversion query into components.

    Accepted formats:
      "100 USD to EUR"
      "100 USD EUR"
      "100 usd in eur"

    Args:
        query: Raw query string from the LLM.

    Returns:
        Tuple of (amount, from_currency, to_currency).

    Raises:
        ValueError: Query cannot be parsed.
    """
    import re

    query = query.strip()

    # Pattern: <number> <FROM> [to|in] <TO>
    pattern = re.compile(
        r"^([\d,]+(?:\.\d+)?)\s+([A-Za-z]{3})\s+(?:to|in|into)?\s*([A-Za-z]{3})$",
        re.IGNORECASE,
    )
    match = pattern.match(query)

    if not match:
        raise ValueError(
            f"Could not parse '{query}'. "
            "Expected format: '100 USD to EUR' or '50 GBP INR'."
        )

    amount_str    = match.group(1).replace(",", "")
    from_currency = match.group(2).upper()
    to_currency   = match.group(3).upper()

    try:
        amount = float(amount_str)
    except ValueError:
        raise ValueError(f"'{amount_str}' is not a valid number.")

    return amount, from_currency, to_currency


@tool
def currency_converter(query: str) -> str:
    """
    Convert an amount between currencies using static exchange rates.

    Format: "<amount> <FROM_CURRENCY> to <TO_CURRENCY>"

    Examples:
        "100 USD to EUR"   → converts 100 US Dollars to Euros
        "50 GBP to INR"    → converts 50 British Pounds to Indian Rupees
        "1000 JPY to USD"  → converts 1000 Japanese Yen to US Dollars

    Supported currencies: USD, EUR, GBP, JPY, CAD, AUD, CHF, CNY, INR,
    MXN, BRL, KRW, SGD, HKD, NOK, SEK, DKK, NZD, ZAR, AED.

    Note: Rates are static approximations for educational use.
    Use a live exchange rate API for financial decisions.

    Args:
        query: Conversion request in the format "<amount> <FROM> to <TO>".

    Returns:
        A formatted conversion result string.
    """
    if not query or not query.strip():
        return "Error: Please provide a conversion query, e.g. '100 USD to EUR'."

    try:
        amount, from_currency, to_currency = _parse_query(query)
        return _convert(amount, from_currency, to_currency)
    except ValueError as exc:
        return f"Error: {exc}"
    except Exception as exc:
        return f"Error performing currency conversion: {exc}"