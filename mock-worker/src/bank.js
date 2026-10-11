import { customerForCard, lookupFixture, supportedOperations } from "./fixtures.js";

export const bankHeaders = {
  "cache-control": "no-store",
  "access-control-allow-origin": "*",
  "access-control-allow-methods": "GET, POST, OPTIONS",
  "access-control-allow-headers": "Content-Type, Authorization, Accept",
};

const required = {
  "cards.list": ["customerNo"],
  "cards.balance": ["cardNumber"],
  "statement.week.get": ["cardNumber"],
  "statement.week.decide": ["cardNumber", "newStatementWeek", "statementDay"],
  "statement.week.change": ["cardNumber", "newStatementWeek", "statementDay"],
  "contact.email.get": ["customerNo", "intAddressInfoType"],
  "statement.pdf.send": ["customerNo", "cardNumber", "currencyType", "statementDate", "emailAddress"],
};

function error(status, code, message, type = "ERROR") {
  const guid = crypto.randomUUID().replaceAll("-", "");
  return Response.json({
    result: {
      guid, returnCode: code, messageText: message, code, message, type,
      details: {
        guid,
        rootCause: status === 404 ? "NotFoundException" : "InvalidArgumentsException",
        errorOrigin: "ivr-gateway-test:ivr-channel-v0",
      },
    },
  }, { status, headers: bankHeaders });
}

function invalid(message) {
  return error(400, "INVALID_ARGUMENTS_ERROR", message);
}

function missing(operation) {
  const errors = {
    "cards.list": ["FB_IVR_CHANNEL_102", "Müşteriye ait geçerli kart bilgisi bulunamadı."],
    "cards.balance": ["FB_IVR_CHANNEL_120", "Kart bakiye servisi hatası: kart bulunamadı"],
    "statement.week.get": ["FB_IVR_CHANNEL_121", "Kart bulunamadı"],
    "statement.pdf.send": ["FB_IVR_CHANNEL_107", "Pdf dokümanı bulunamadı"],
  };
  const entry = errors[operation];
  return entry ? error(404, entry[0], entry[1], "WARNING") : invalid("Requested record not found");
}

const nonemptyScalar = (value) =>
  (typeof value === "string" && value.trim() !== "") ||
  (typeof value === "number" && Number.isFinite(value));

// Exact section 6 fixture lookup. No stateful mutations or customer-policy decisions.
export async function bankFetch(request, sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))) {
  const path = new URL(request.url).pathname;
  if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: bankHeaders });
  const operation = path.startsWith("/voice/v1/") ? path.slice("/voice/v1/".length) : "";
  if (!supportedOperations.includes(operation)) {
    return Response.json({ error: "not_found" }, { status: 404, headers: bankHeaders });
  }
  if (request.method !== "POST") {
    return Response.json({ error: "method_not_allowed" }, {
      status: 405, headers: { ...bankHeaders, allow: "POST, OPTIONS" },
    });
  }
  let body;
  try {
    body = await request.json();
  } catch {
    return invalid("Request body must be valid JSON");
  }
  const data = body?.data;
  if (!data || typeof data !== "object" || Array.isArray(data)) return invalid("data is required");
  for (const field of required[operation]) {
    if (!Object.hasOwn(data, field) || !nonemptyScalar(data[field])) {
      return invalid(`${field === "cardNumber" ? "card number" : field} cannot be empty`);
    }
  }
  let selectors = [];
  let customerNo = data.customerNo;
  if (["cards.balance", "statement.week.get", "statement.week.decide", "statement.week.change"].includes(operation)) {
    customerNo = customerForCard(data.cardNumber);
    selectors = [data.cardNumber];
  }
  if (operation === "statement.week.decide" || operation === "statement.week.change") {
    const week = Number(data.newStatementWeek);
    const day = Number(data.statementDay);
    if (!Number.isInteger(week) || week < 1 || week > 4) return invalid("newStatementWeek must be 1-4");
    if (!Number.isInteger(day) || day < 1 || day > 31) return invalid("statementDay must be 1-31");
    selectors.push(String(week));
  }
  if (operation === "statement.pdf.send") {
    if (!["TL", "USD", "EUR", "GBP"].includes(data.currencyType)) return invalid("Invalid currencyType");
    if (customerForCard(data.cardNumber) !== String(customerNo)) return missing(operation);
  }
  const fixture = lookupFixture(customerNo, operation, selectors);
  if (!fixture) return missing(operation);
  if (fixture.delaySeconds) await sleep(fixture.delaySeconds * 1000);
  return Response.json(fixture.response, { status: fixture.http, headers: bankHeaders });
}
