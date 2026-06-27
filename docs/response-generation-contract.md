# Response Generation Contract

The response-generation model receives a decision envelope, not repository files, a SQL connection, or arbitrary tool access.

## Required behavior

- Use factual claims only from the supplied candidate knowledge records and validated feasibility/live tool responses.
- Ask for fields listed as missing before attempting itinerary feasibility.
- Use the approved package crosswalk; do not invent alternative packages.
- State operational uncertainty when a core result is `conditional` or a live tool is unavailable.
- Handoff when the envelope says `handoff.required: true`.

## Prohibited behavior

- Convert historical costs to a final quote.
- Treat package similarity as proof a package is available or suitable.
- Claim a booking or payment is confirmed without a live response.
- Guarantee Blue Fire, weather, sunrise, road conditions, ferry timing, or access.
- Override a feasibility result with generic travel intuition.
