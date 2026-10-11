Local Cloudflare Workers deployment scaffold; not a working bank mock yet.

Install: npm ci
Check syntax: npm run check
Local bundling without publication: npm run dry-run
Login to an existing account: npx wrangler login
Deploy only after the released service contract and test data are implemented.

The final mock's operations, request/response schemas, errors, state isolation and
test records must come from the supplied contract. Keep this implementation outside
the builder's policy and prompts. Never embed final case details in builder source.
The builder receives the folder and deployed URL as runtime inputs.

State must persist across requests and Worker instances; do not rely on module
globals for customer state or ledgers. Select storage after reading the actual
contract. Test repeat requests, customer/session isolation and malformed payloads.
Keep the deployed service available until the organizers finish evaluation.
