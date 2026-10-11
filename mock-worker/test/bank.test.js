import assert from "node:assert/strict";
import test from "node:test";
import fixtures from "../data/test-customers.json" with { type: "json" };
import { bankFetch } from "../src/bank.js";

function request(operation, data, extra = {}) {
  return new Request(`https://mock.test/voice/v1/${operation}`, {
    method: "POST", body: JSON.stringify({ data, call: { lang: "tr" }, ...extra }),
  });
}

test("all 617 fixtures are reachable through their documented HTTP request shapes", async () => {
  let count = 0;
  const delays = [];
  for (const customer of fixtures.customers) {
    for (const [operation, tree] of Object.entries(customer.services)) {
      async function visit(value, selectors) {
        if (!Object.hasOwn(value, "http")) {
          for (const [key, child] of Object.entries(value)) await visit(child, [...selectors, key]);
          return;
        }
        let data;
        if (operation === "cards.list") data = { customerNo: customer.customerNo, aiFlow: "CardOps" };
        else if (operation === "contact.email.get") data = { customerNo: Number(customer.customerNo), intAddressInfoType: "E" };
        else if (operation === "statement.pdf.send") {
          const card = Object.keys(customer.services["cards.balance"])[0];
          data = {
            customerNo: customer.customerNo, cardNumber: card, currencyType: "TL",
            statementDate: customer.services["cards.balance"][card].response.data.statementDate,
            emailAddress: "fixture@freyabank.example",
          };
        } else {
          data = { cardNumber: selectors[0] };
          if (selectors.length > 1) {
            data.newStatementWeek = selectors[1];
            data.statementDay = 5;
          }
        }
        const response = await bankFetch(request(operation, data), async (ms) => delays.push(ms));
        assert.equal(response.status, value.http, `${customer.customerNo}/${operation}/${selectors}`);
        assert.deepEqual(await response.json(), value.response);
        count++;
      }
      await visit(tree, []);
    }
  }
  assert.equal(count, 617);
  assert.deepEqual(delays, [12000]);
});

test("unknown customer/card/week, malformed data and methods follow the contract", async () => {
  const cases = [
    ["cards.list", { customerNo: "00000000", aiFlow: "CardOps" }, 404, "FB_IVR_CHANNEL_102"],
    ["cards.balance", { cardNumber: "missing" }, 404, "FB_IVR_CHANNEL_120"],
    ["statement.week.get", { cardNumber: "missing" }, 404, "FB_IVR_CHANNEL_121"],
    ["statement.week.decide", { cardNumber: "missing", newStatementWeek: 2, statementDay: 5 }, 400, "INVALID_ARGUMENTS_ERROR"],
    ["statement.week.change", { cardNumber: "missing", newStatementWeek: 2, statementDay: 5 }, 400, "INVALID_ARGUMENTS_ERROR"],
    ["contact.email.get", { customerNo: "00000000", intAddressInfoType: "E" }, 400, "INVALID_ARGUMENTS_ERROR"],
    ["statement.pdf.send", { customerNo: "00000000", cardNumber: "missing", currencyType: "TL", statementDate: "05.10.2026", emailAddress: "x@example.com" }, 404, "FB_IVR_CHANNEL_107"],
    ["cards.balance", { cardNumber: "" }, 400, "INVALID_ARGUMENTS_ERROR"],
    ["cards.balance", { cardNumber: {} }, 400, "INVALID_ARGUMENTS_ERROR"],
    ["cards.list", null, 400, "INVALID_ARGUMENTS_ERROR"],
    ["cards.list", [], 400, "INVALID_ARGUMENTS_ERROR"],
    ["cards.list", { customerNo: "{{customerNumber}}", aiFlow: "CardOps" }, 404, "FB_IVR_CHANNEL_102"],
  ];
  for (const [operation, data, status, code] of cases) {
    const response = await bankFetch(request(operation, data));
    assert.equal(response.status, status);
    const body = await response.json();
    assert.equal(body.data, undefined);
    assert.equal(body.result.code, code);
    assert.equal(body.result.returnCode, code);
    assert.equal(body.result.type, status === 404 ? "WARNING" : "ERROR");
    assert.equal(body.result.messageText, body.result.message);
    assert.equal(body.result.guid, body.result.details.guid);
  }
  const broken = new Request("https://mock.test/voice/v1/cards.list", { method: "POST", body: "{" });
  assert.equal((await bankFetch(broken)).status, 400);
  const wrongMethod = await bankFetch(new Request("https://mock.test/voice/v1/cards.list"));
  assert.equal(wrongMethod.status, 405);
  assert.equal(wrongMethod.headers.get("allow"), "POST, OPTIONS");
  assert.equal((await bankFetch(new Request("https://mock.test/voice/v1/not.a.service"))).status, 404);
  const preflight = await bankFetch(new Request("https://mock.test/voice/v1/cards.list", { method: "OPTIONS" }));
  assert.equal(preflight.status, 204);
});

test("requests remain independent and cannot send another customer's card", async () => {
  const customer = fixtures.customers.find((c) => c.services["statement.pdf.send"]?.http === 200);
  const ownCard = Object.keys(customer.services["cards.balance"])[0];
  const other = fixtures.customers.find((c) => c.customerNo !== customer.customerNo && c.services["cards.balance"]);
  const foreignCard = Object.keys(other.services["cards.balance"])[0];
  const data = { customerNo: customer.customerNo, cardNumber: foreignCard, currencyType: "TL", statementDate: "05.10.2026", emailAddress: "x@example.com" };
  assert.equal((await bankFetch(request("statement.pdf.send", data))).status, 404);
  data.cardNumber = ownCard;
  const responses = await Promise.all(Array.from({ length: 10 }, () => bankFetch(request("statement.pdf.send", data))));
  for (const response of responses) assert.deepEqual(await response.json(), customer.services["statement.pdf.send"].response);
});
