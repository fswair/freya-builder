// Deployment scaffold only. Implement the supplied service contract after release.
// Failing explicitly prevents this scaffold from masquerading as a working bank.
export default {
  async fetch(request) {
    const path = new URL(request.url).pathname;
    const health = request.method === "GET" && path === "/health";
    return Response.json(
      {
        ready: false,
        error: health ? "service_contract_not_configured" : "operation_not_implemented",
      },
      { status: health ? 503 : 501, headers: { "cache-control": "no-store" } },
    );
  },
};
