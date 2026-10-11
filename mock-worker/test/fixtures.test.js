import assert from "node:assert/strict";
import test from "node:test";
import fixtures from "../data/test-customers.json" with { type: "json" };
import { lookupFixture, supportedOperations } from "../src/fixtures.js";

test("all published customer/card/week responses retain exact bodies, statuses and delays", () => {
  let count = 0;
  const statuses = new Set();
  const delays = [];
  for (const customer of fixtures.customers) {
    for (const [operation, responses] of Object.entries(customer.services)) {
      function visit(value, selectors) {
        if (Object.hasOwn(value, "http")) {
          const actual = lookupFixture(customer.customerNo, operation, selectors);
          assert.deepEqual(actual, value, `${customer.customerNo}/${operation}/${selectors.join("/")}`);
          assert.notEqual(actual, value);
          statuses.add(actual.http);
          if (actual.delaySeconds) delays.push(actual.delaySeconds);
          count += 1;
          return;
        }
        for (const [key, child] of Object.entries(value)) visit(child, [...selectors, key]);
      }
      visit(responses, []);
    }
  }
  assert.equal(count, 617);
  assert.equal(supportedOperations.length, 7);
  assert.deepEqual([...statuses].sort(), [200, 404, 500]);
  assert.deepEqual(delays, [12]);
});

test("unknown selectors never fall back to another customer's data", () => {
  const customer = fixtures.customers[0];
  assert.equal(lookupFixture("not-a-customer", "cards.list"), null);
  for (const operation of ["__proto__", "constructor", "toString", "unknown"]) {
    assert.equal(lookupFixture(customer.customerNo, operation), null);
  }
  for (const selectors of [[], ["unknown-card"], ["__proto__"], ["constructor"]]) {
    assert.equal(lookupFixture(customer.customerNo, "cards.balance", selectors), null);
  }
  const response = lookupFixture(customer.customerNo, "cards.list");
  response.response.data.cards.length = 0;
  assert.deepEqual(lookupFixture(customer.customerNo, "cards.list"), customer.services["cards.list"]);
});
