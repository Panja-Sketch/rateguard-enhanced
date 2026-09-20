"""Semantic vocabulary for RG_METADATA (currency and jurisdiction).

`RECOGNIZED_*` is what the standards define (ISO 4217 currencies, ISO 3166-2:US
subdivisions); `SUPPORTED_*` is what this deployment's product scope actually
prices. A value must be *recognized* to be meaningful and *supported* to be
accepted -- an unsupported value is rejected with a specific code and is never
normalized into a supported one (locked doc 6.2/6.4: no silent approximation).

Current scope (locked doc section 1): the US Arizona homeowners product,
priced in USD. Widening it is a one-line, reviewed change to the constants
below, not something a workbook can do by declaring a value.
"""

RECOGNIZED_CURRENCIES = frozenset(
    "AED AFN ALL AMD ANG AOA ARS AUD AWG AZN BAM BBD BDT BGN BHD BIF BMD BND BOB BRL BSD BTN BWP BYN BZD CAD "
    "CDF CHF CLP CNY COP CRC CUP CVE CZK DJF DKK DOP DZD EGP ERN ETB EUR FJD FKP GBP GEL GHS GIP GMD GNF GTQ "
    "GYD HKD HNL HTG HUF IDR ILS INR IQD IRR ISK JMD JOD JPY KES KGS KHR KMF KPW KRW KWD KYD KZT LAK LBP LKR "
    "LRD LSL LYD MAD MDL MGA MKD MMK MNT MOP MRU MUR MVR MWK MXN MYR MZN NAD NGN NIO NOK NPR NZD OMR PAB PEN "
    "PGK PHP PKR PLN PYG QAR RON RSD RUB RWF SAR SBD SCR SDG SEK SGD SHP SLE SOS SRD SSP STN SVC SYP SZL THB "
    "TJS TMT TND TOP TRY TTD TWD TZS UAH UGX USD UYU UZS VES VND VUV WST XAF XCD XOF XPF YER ZAR ZMW ZWL".split()
)
SUPPORTED_CURRENCIES = frozenset({"USD"})

RECOGNIZED_JURISDICTIONS: dict[str, frozenset[str]] = {
    "US": frozenset(
        "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND "
        "OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC".split()
    ),
}
SUPPORTED_JURISDICTIONS: dict[str, frozenset[str]] = {"US": frozenset({"AZ"})}
