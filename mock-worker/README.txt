Freya Bank synthetic mock, implementing the 1 October 2026 service contract.

Install: npm ci
Check syntax: npm run check
Check real local runtime, storage isolation and restart persistence: npm test
Local bundling without publication: npm run dry-run
Login to an existing account: npx wrangler login
Deploy: npm run deploy

GET /health and /ready check SQLite-backed infrastructure and report ready=true.
The seven POST /voice/v1/{service} routes use the documented data/call envelope.
Tokens, headers and call metadata are ignored in the official test environment.

data/test-customers.json is an unchanged copy of the organizer's synthetic response
table. src/fixtures.js resolves customer/card/week records without inventing data.
Its 617 responses are checked through both fixture lookup and HTTP request shapes,
including exact error bodies and the specified 12-second delay. Unknown records
return the service-specific 404, or INVALID_ARGUMENTS_ERROR where no 404 is defined.
Customer and card lookups never fall back to another record or a preview customer.
Card identifiers are passed unchanged; the differently masked balance echo is not
used as a lookup key. PDF requests also check card/customer ownership.

The final mock's operations, request/response schemas, errors, state isolation and
test records must come from the supplied contract. Keep this implementation outside
the builder's policy and prompts. Never embed final case details in builder source.
The builder receives the folder and deployed URL as runtime inputs.

The bank responses are static, as specified by the official mock README; operations
do not mutate them. BANK_STATE is only the SQLite-backed infrastructure health probe.
Keep the deployed service available until the organizers finish evaluation.
