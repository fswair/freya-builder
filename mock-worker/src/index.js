import { DurableObject } from "cloudflare:workers";

const headers = { "cache-control": "no-store" };

function unavailable() {
  return Response.json(
    { ready: false, error: "service_contract_not_configured" },
    { status: 503, headers },
  );
}

// SQLite-backed storage is independent of any final-case fields or endpoints.
// Bank operations will use scoped instances once the service contract is available.
export class BankState extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    ctx.blockConcurrencyWhile(async () => {
      if (!(await ctx.storage.get("installation_id"))) {
        await ctx.storage.put("installation_id", crypto.randomUUID());
      }
    });
  }

  async probe() {
    const installationId = await this.ctx.storage.get("installation_id");
    if (!installationId) throw new Error("Storage initialization failed");
    return { installationId };
  }
}

export default {
  async fetch(request, env) {
    const path = new URL(request.url).pathname;
    if (path === "/health") {
      if (request.method !== "GET") {
        return Response.json(
          { error: "method_not_allowed" },
          { status: 405, headers: { ...headers, allow: "GET" } },
        );
      }
      try {
        await env.BANK_STATE.getByName("infrastructure").probe();
        return Response.json(
          { service: "freya-bank-mock", storage: "ok", ready: false, status: "awaiting_contract" },
          { headers },
        );
      } catch {
        return Response.json(
          { ready: false, error: "storage_unavailable" },
          { status: 503, headers },
        );
      }
    }
    // /ready and every bank operation fail closed until the exact contract lands.
    return unavailable();
  },
};
