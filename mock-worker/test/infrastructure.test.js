import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { Miniflare, convertV4MiniflareOptions } from "miniflare";
import fixtures from "../data/test-customers.json" with { type: "json" };

function runtime(storage) {
  return new Miniflare(convertV4MiniflareOptions({
    name: "freya-hackathon-mock",
    modules: true,
    scriptPath: fileURLToPath(new URL("../dist/index.js", import.meta.url)),
    compatibilityDate: "2026-10-11",
    durableObjects: { BANK_STATE: { className: "BankState", useSQLite: true } },
    resourcePersistencePath: storage,
    telemetry: { enabled: false },
    cf: false,
  }));
}

test("health verifies storage and bank routes are ready", async () => {
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
        service: "freya-bank-mock", storage: "ok", ready: true, status: "ready",
      });
    }
    assert.equal((await mf.dispatchFetch("https://mock.test/ready")).status, 200);
    for (const path of ["/", "/unknown-bank-operation"]) {
      const response = await mf.dispatchFetch(`https://mock.test${path}`, {
        method: "POST", body: "untrusted request body",
      });
      assert.equal(response.status, 404);
      assert.deepEqual(await response.json(), { error: "not_found" });
    }
    const wrongMethod = await mf.dispatchFetch("https://mock.test/health", { method: "POST" });
    assert.equal(wrongMethod.status, 405);
    assert.equal(wrongMethod.headers.get("allow"), "GET");
  } finally {
    await mf.dispose();
    await rm(storage, { recursive: true, force: true });
  }
});

test("the bundled Workers runtime serves all seven routes and the real delayed response", async () => {
  const storage = await mkdtemp(join(tmpdir(), "freya-worker-bank-"));
  const mf = runtime(storage);
  try {
    const customer = fixtures.customers.find((c) => Object.keys(c.services).length === 7 && c.services["statement.pdf.send"].http === 200);
    const card = Object.keys(customer.services["cards.balance"])[0];
    const week = Object.keys(customer.services["statement.week.decide"][card])[0];
    const requests = [
      ["cards.list", { customerNo: customer.customerNo, aiFlow: "CardOps" }, customer.services["cards.list"]],
      ["cards.balance", { cardNumber: card }, customer.services["cards.balance"][card]],
      ["statement.week.get", { cardNumber: card }, customer.services["statement.week.get"][card]],
      ["statement.week.decide", { cardNumber: card, newStatementWeek: week, statementDay: 5 }, customer.services["statement.week.decide"][card][week]],
      ["statement.week.change", { cardNumber: card, newStatementWeek: week, statementDay: 5 }, customer.services["statement.week.change"][card][week]],
      ["contact.email.get", { customerNo: customer.customerNo, intAddressInfoType: "B" }, customer.services["contact.email.get"]],
      ["statement.pdf.send", { customerNo: customer.customerNo, cardNumber: card, currencyType: "TL", statementDate: "05.10.2026", emailAddress: "fixture@freyabank.example" }, customer.services["statement.pdf.send"]],
    ];
    for (const [operation, data, expected] of requests) {
      const response = await mf.dispatchFetch(`https://mock.test/voice/v1/${operation}`, {
        method: "POST", body: JSON.stringify({ data }),
      });
      assert.equal(response.status, expected.http, operation);
      assert.deepEqual(await response.json(), expected.response);
    }
    const delayed = fixtures.customers.flatMap((c) => Object.entries(c.services["cards.balance"] ?? {})).find(([, value]) => value.delaySeconds);
    const started = performance.now();
    const response = await mf.dispatchFetch("https://mock.test/voice/v1/cards.balance", {
      method: "POST", body: JSON.stringify({ data: { cardNumber: delayed[0] } }),
    });
    assert.ok(performance.now() - started >= 11500, "the actual 12-second fixture delay was honored");
    assert.equal(response.status, delayed[1].http);
    assert.deepEqual(await response.json(), delayed[1].response);
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
