import { DurableObject } from "cloudflare:workers";
import { bankFetch, bankHeaders } from "./bank.js";

const headers = bankHeaders;

// SQLite-backed storage is independent of any final-case fields or endpoints.
// The official bank responses are static; this object only probes infrastructure.
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
    if (request.method === "OPTIONS") return bankFetch(request);
    if (path === "/health" || path === "/ready") {
      if (request.method !== "GET") {
        return Response.json(
          { error: "method_not_allowed" },
          { status: 405, headers: { ...headers, allow: "GET" } },
        );
      }
      try {
        await env.BANK_STATE.getByName("infrastructure").probe();
        return Response.json(
          { service: "freya-bank-mock", storage: "ok", ready: true, status: "ready" },
          { headers },
        );
      } catch {
        return Response.json(
          { ready: false, error: "storage_unavailable" },
          { status: 503, headers },
        );
      }
    }
    return bankFetch(request);
  },
};
