Cloudflare Workers infrastructure; the bank service contract is still pending.

Install: npm ci
Check syntax: npm run check
Check real local runtime, storage isolation and restart persistence: npm test
Local bundling without publication: npm run dry-run
Login to an existing account: npx wrangler login
Deploy: npm run deploy

GET /health checks the SQLite-backed Durable Object storage and reports ready=false.
GET /ready and unimplemented bank routes return HTTP 503 until the contract lands.
An infrastructure deployment is not a completed mock and must not be submitted as one.

The final mock's operations, request/response schemas, errors, state isolation and
test records must come from the supplied contract. Keep this implementation outside
the builder's policy and prompts. Never embed final case details in builder source.
The builder receives the folder and deployed URL as runtime inputs.

The BANK_STATE binding uses SQLite-backed Durable Objects. Scope the bank's state
according to the supplied contract; do not rely on module globals for customer
state or ledgers. Test repeat requests, customer/session isolation and malformed payloads.
Keep the deployed service available until the organizers finish evaluation.
