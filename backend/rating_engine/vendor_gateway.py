"""Wire-shape translation for the "vendor gateway" demo target.

This is the second, deliberately differently-shaped connector target
referenced in the README's "Live Connector / Black-Box API Validation"
section: a nested, camelCase request/response envelope modeled on how a
policy-admin-system-style vendor quote API is commonly shaped, as opposed to
this repo's own flat `rating_engine.models.QuoteRequest`/`QuoteResponse`
contract. It rates through the exact same deterministic engine
(`rating_engine.engines.quote_service.execute_quote`) as `/quote` -- only the
wire envelope differs -- because the point being proven is that
RateGuard's connector client can adapt to a *different wire shape*, not that
a second pricing implementation exists.
"""

from rating_engine.engines.quote_service import execute_quote
from rating_engine.models import (
    QuoteRequest,
    VendorGatewayQuoteRequest,
    VendorGatewayQuoteResponse,
    VendorPolicyResponse,
)


def handle_vendor_quote(request: VendorGatewayQuoteRequest) -> VendorGatewayQuoteResponse:
    policy = request.policyRequest
    quote_request = QuoteRequest(
        request_id=policy.correlationId,
        engine_version=policy.engineVersion,
        product_id=policy.productCode,
        effective_date=policy.asOfDate,
        transaction_type=policy.transactionType,
        inputs=policy.ratingFactors,
    )
    quote_response = execute_quote(quote_request)
    return VendorGatewayQuoteResponse(
        policyResponse=VendorPolicyResponse(
            correlationId=quote_response.request_id,
            engineVersion=quote_response.engine_version,
            premiumComponents=quote_response.outputs,
            quotedAt=quote_response.rated_at,
        )
    )
