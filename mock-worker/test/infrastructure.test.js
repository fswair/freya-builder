import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { Miniflare, convertV4MiniflareOptions } from "miniflare";

function runtime(storage) {
  return new Miniflare(convertV4MiniflareOptions({
    name: "freya-hackathon-mock",
    modules: true,
    scriptPath: fileURLToPath(new URL("../src/index.js", import.meta.url)),
    compatibilityDate: "2026-10-11",
    durableObjects: { BANK_STATE: { className: "BankState", useSQLite: true } },
    resourcePersistencePath: storage,
    telemetry: { enabled: false },
    cf: false,
  }));
}

test("health verifies storage while incomplete bank operations stay unavailable", async () => {
  const storage = await mkdtemp(join(tmpdir(), "freya-worker-health-"));
  const mf = runtime(storage);
  try {
    const responses = await Promise.all(
      Array.from({ length: 5 }, () => mf.dispatchFetch("https://mock.test/health")),
    );
    for (const response of responses) {
      assert.equal(response.status, 200);
      assert.equal(response.headers.get("cache-control"), "no-store");
      assert.deepEqual(await response.json(), {
        service: "freya-bank-mock", storage: "ok", ready: false, status: "awaiting_contract",
      });
    }
    for (const path of ["/ready", "/", "/unknown-bank-operation"]) {
      const response = await mf.dispatchFetch(`https://mock.test${path}`, {
        method: "POST", body: "untrusted request body",
      });
      assert.equal(response.status, 503);
      assert.deepEqual(await response.json(), {
        ready: false, error: "service_contract_not_configured",
      });
    }
    const wrongMethod = await mf.dispatchFetch("https://mock.test/health", { method: "POST" });
    assert.equal(wrongMethod.status, 405);
    assert.equal(wrongMethod.headers.get("allow"), "GET");
  } finally {
    await mf.dispose();
    await rm(storage, { recursive: true, force: true });
  }
});

test("SQLite state stays isolated and survives a fresh runtime", async () => {
  const storage = await mkdtemp(join(tmpdir(), "freya-worker-persistence-"));
  let mf;
  try {
    mf = runtime(storage);
    let namespace = await mf.getDurableObjectNamespace("BANK_STATE");
    const first = (await namespace.get(namespace.idFromName("scope-a")).probe()).installationId;
    const other = (await namespace.get(namespace.idFromName("scope-b")).probe()).installationId;
    assert.match(first, /^[0-9a-f-]{36}$/);
    assert.notEqual(first, other);
    await mf.dispose();
    mf = runtime(storage);
    namespace = await mf.getDurableObjectNamespace("BANK_STATE");
    assert.equal((await namespace.get(namespace.idFromName("scope-a")).probe()).installationId, first);
    assert.equal((await namespace.get(namespace.idFromName("scope-b")).probe()).installationId, other);
  } finally {
    if (mf) await mf.dispose();
    await rm(storage, { recursive: true, force: true });
  }
});
