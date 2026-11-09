# 06 — Countries and Localisation

## 1. Principle

Country-specific behaviour lives in **data** (country configuration, reference tables) and in **provider adapters** behind interfaces. Shared business logic asks questions ("what tax rates apply to this branch on this date?", "which payment providers are available?") and never contains `if country == "UG"`.

## 2. Where each setting lives

| Setting | Country default | Organisation | Branch | User |
|---------|----------------|--------------|--------|------|
| Legal jurisdiction | — | ✔ (country of registration) | ✔ (country of operation) | — |
| Currency | default | base currency | — (transactions record their own) | — |
| Timezone | default | default | ✔ overrides org | ✔ display preference |
| Language / locale | supported list | default | — | ✔ preference |
| Date / number format | ✔ | override | — | — (derived from locale) |
| Tax rates | ✔ (per jurisdiction, effective dates) | registrations (TIN, VAT status) | jurisdiction from branch country | — |
| Financial year start | ✔ | override | — | — |
| Payment providers | available list | enabled + credentials | — | — |

Branch has its own country because an organisation can operate in more than one country (e.g. a Ugandan company with a Nairobi branch). Tax and payment-provider selection follow the branch's country; reporting follows the organisation's base currency.

## 3. Reference data

- **Country** (ISO 3166-1 alpha-2), **Currency** (ISO 4217 with minor units), **Language** (BCP 47), timezones (IANA names, validated with `zoneinfo`).
- Loaded by a data migration / management command from versioned fixture files, not edited by hand in production. Only enabled countries are offered at sign-up.
- **CountryConfiguration** holds formats, address format, financial year, provider codes and regulatory flags. It is versioned (`version` integer) and cached; changes are audited.
- Launching a new market = adding fixture data + enabling provider adapters for that country. No core code changes.

## 4. Money

- `Money` value object in `common/db/money.py`: `Decimal` amount + ISO currency code; arithmetic between different currencies raises an error.
- Storage: `NUMERIC(19,4)` amount + `CHAR(3)` currency on every monetary record. Four decimal places allows intermediate precision (unit costs, tax); amounts are rounded to the currency's minor units (UGX 0, KES 2, USD 2, KWD 3) at defined points (line totals, document totals) using `ROUND_HALF_UP` unless a country configuration specifies otherwise.
- API representation: `{"amount": "12000.00", "currency": "UGX"}`, the amount as a **string** so JavaScript clients do not lose precision.
- **No automatic currency conversion.** When multi-currency operations are needed, an `ExchangeRate` table (source, date, rate) and an explicit conversion recorded on the transaction will be added. Historical records keep their original amount and currency forever.

## 5. Time

- `USE_TZ=True`, `TIME_ZONE="UTC"`. All timestamps stored as `timestamptz` in UTC.
- Business dates (a sale's trading day, a report's "today", expiry checks, an appointment slot) are computed in the **branch timezone**, falling back to the organisation timezone. Helper: `local_date(ctx, branch)` — never `timezone.localdate()` with the server default.
- The API returns ISO 8601 UTC timestamps (`2026-11-09T07:30:00Z`); clients format them for display. Calendar-type fields (birth date, expiry date) are plain dates.

## 6. Language and formatting

- Django i18n (`gettext`) for API error messages and system labels; `LocaleMiddleware` + user preference + `Accept-Language`. English is the source language; translations added per market.
- Error responses always include a stable machine-readable `code` alongside the translated message, so clients can translate on their side as well.
- **The API returns raw values, not formatted strings.** Number and date formatting is the client's job. Server-side formatting (PDF invoices, emails, SMS) uses **Babel** with the recipient's locale.
- Notification templates are stored per `(key, channel, locale)` with fallback to the default locale.

## 7. Addresses and phone numbers

- Phone numbers stored in E.164, parsed with the organisation/branch country as the default region.
- Address: `line1`, `line2`, `locality`, `region`, `postal_code` (optional, many markets do not use them), `country_code`, plus `directions` (landmark-based directions are common in East Africa). The country's `address_format` decides which fields are required and the display order.

## 8. Regulatory boundaries

Tax, invoicing (e.g. electronic fiscal receipting), payment providers and data protection differ by country. Each is a separate interface with per-country adapters:

| Boundary | Interface (example) | First adapter |
|----------|---------------------|---------------|
| Tax calculation | `TaxCalculator.calculate(lines, jurisdiction, date)` | Simple rate table |
| Fiscal invoicing | `FiscalInvoiceProvider.submit(invoice)` | None yet (e.g. Uganda URA EFRIS requires investigation) |
| Payments | `PaymentProvider.initiate / verify / refund` | Cash + one mobile-money provider (Phase 4/5) |
| SMS | `SmsGateway.send` | One regional gateway |

Supporting configuration is not the same as being compliant. Each market launch needs a legal and tax review; this design does not claim compliance with any regulation.
