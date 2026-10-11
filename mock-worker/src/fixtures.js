import fixtures from "../data/test-customers.json" with { type: "json" };

const customers = new Map(fixtures.customers.map((customer) => [customer.customerNo, customer]));
const cardOwners = new Map();
for (const customer of fixtures.customers) {
  for (const operation of ["cards.balance", "statement.week.get", "statement.week.decide", "statement.week.change"]) {
    for (const card of Object.keys(customer.services[operation] ?? {})) {
      const previous = cardOwners.get(card);
      if (previous && previous !== customer.customerNo) throw new Error("Ambiguous fixture card");
      cardOwners.set(card, customer.customerNo);
    }
  }
}

export const supportedOperations = Object.freeze([
  ...new Set(fixtures.customers.flatMap((customer) => Object.keys(customer.services))),
]);

function own(record, key) {
  return record && typeof record === "object" && Object.hasOwn(record, key) ? record[key] : undefined;
}

export function customerForCard(cardNumber) {
  return cardOwners.get(String(cardNumber)) ?? null;
}

// Response bodies are immutable snapshots, including deliberate errors and delays.
export function lookupFixture(customerNo, operation, selectors = []) {
  const customer = customers.get(String(customerNo));
  let value = own(customer?.services, operation);
  for (const selector of selectors) value = own(value, String(selector));
  if (!value || !Object.hasOwn(value, "http") || !Object.hasOwn(value, "response")) return null;
  // Never let one request mutate the canonical answers observed by another call.
  return structuredClone(value);
}
