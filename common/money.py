"""Money: an exact decimal amount with an explicit ISO 4217 currency.

- Never float. Arithmetic between different currencies is an error, not a silent
  conversion (docs/architecture/06-localisation.md §4).
- Stored as ``NUMERIC(19,4)`` + ``CHAR(3)``; rounded to the currency's minor units
  (UGX 0, KES 2, KWD 3...) only at defined points, using ``round_to``.
- Serialised as ``{"amount": "12000.00", "currency": "UGX"}``: the amount is a string so
  JavaScript clients never lose precision.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from django.db import models

AMOUNT_MAX_DIGITS = 19
AMOUNT_DECIMAL_PLACES = 4


class CurrencyMismatch(ValueError):
    pass


def _to_decimal(value: Decimal | int | str) -> Decimal:
    if isinstance(value, float):
        raise TypeError("Money amounts must not be floats; use Decimal or str.")
    try:
        result = Decimal(value)
    except (InvalidOperation, TypeError) as exc:
        raise ValueError(f"Invalid money amount: {value!r}") from exc
    if not result.is_finite():
        raise ValueError("Money amounts must be finite.")
    return result


@dataclass(frozen=True, slots=True)
class Money:
    amount: Decimal
    currency: str

    def __init__(self, amount: Decimal | int | str, currency: str) -> None:
        if len(currency) != 3 or not currency.isalpha() or not currency.isupper():
            raise ValueError(f"Invalid currency code: {currency!r}")
        object.__setattr__(self, "amount", _to_decimal(amount))
        object.__setattr__(self, "currency", currency)

    def _same(self, other: Money) -> None:
        if not isinstance(other, Money):
            raise TypeError("Money can only be combined with Money.")
        if other.currency != self.currency:
            raise CurrencyMismatch(f"Cannot combine {self.currency} with {other.currency}.")

    def __add__(self, other: Money) -> Money:
        self._same(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._same(other)
        return Money(self.amount - other.amount, self.currency)

    def __mul__(self, factor: Decimal | int) -> Money:
        if isinstance(factor, float | Money):
            raise TypeError("Multiply Money by a Decimal or int.")
        return Money(self.amount * Decimal(factor), self.currency)

    __rmul__ = __mul__

    def __neg__(self) -> Money:
        return Money(-self.amount, self.currency)

    def __lt__(self, other: Money) -> bool:
        self._same(other)
        return self.amount < other.amount

    def __le__(self, other: Money) -> bool:
        self._same(other)
        return self.amount <= other.amount

    def round_to(self, minor_units: int) -> Money:
        """Round half-up to the currency's minor units (use localisation for the value)."""
        quantum = Decimal(1).scaleb(-minor_units)
        return Money(self.amount.quantize(quantum, rounding=ROUND_HALF_UP), self.currency)

    def as_dict(self, minor_units: int | None = None) -> dict[str, str]:
        amount = self.round_to(minor_units).amount if minor_units is not None else self.amount
        return {"amount": str(amount), "currency": self.currency}

    @classmethod
    def zero(cls, currency: str) -> Money:
        return cls(Decimal(0), currency)


def money_amount_field(**kwargs: Any) -> models.DecimalField[Decimal, Decimal]:
    """The standard column for money amounts; pair it with a 3-letter currency column."""
    return models.DecimalField(
        max_digits=AMOUNT_MAX_DIGITS, decimal_places=AMOUNT_DECIMAL_PLACES, **kwargs
    )
