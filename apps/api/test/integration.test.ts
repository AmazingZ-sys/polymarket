import { test, after, before } from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { app } from "../src/app.js";
import { db, redis } from "../src/db.js";
import { bootstrap } from "../src/security.js";
import { resources } from "../src/resources.js";
const suffix = randomUUID().slice(0, 8);
process.env.ADMIN_USERNAME = "test-admin-" + suffix;
process.env.VIEWER_USERNAME = "test-view-" + suffix;
process.env.ADMIN_PASSWORD = randomUUID();
process.env.VIEWER_PASSWORD = randomUUID();
let cookie = "",
  viewer = "",
  account = "",
  clone = "";
function post(
  path: string,
  body: unknown,
  auth = cookie,
  key = randomUUID(),
  origin?: string,
) {
  return app.request("/api/v1/" + path, {
    method: "POST",
    headers: {
      cookie: auth,
      "Content-Type": "application/json",
      "Idempotency-Key": key,
      ...(origin ? { origin } : {}),
    },
    body: JSON.stringify(body),
  });
}
const get = (path: string, auth = cookie) =>
  app.request("/api/v1/" + path, { headers: { cookie: auth } });
before(async () => {
  if (!process.env.DATABASE_URL?.includes("test"))
    throw Error("Use a dedicated test database");
  await redis.connect();
  await bootstrap();
  for (const [user, pass, readonly] of [
    [process.env.ADMIN_USERNAME, process.env.ADMIN_PASSWORD, false],
    [process.env.VIEWER_USERNAME, process.env.VIEWER_PASSWORD, true],
  ]) {
    const r = await post("auth/login", { username: user, password: pass }, "");
    assert.equal(r.status, 200);
    const token = r.headers.get("set-cookie")!.split(";")[0];
    if (readonly) viewer = token;
    else cookie = token;
  }
});
after(async () => {
  await redis.quit();
  await db.end();
});
test("session, role, origin and no LIVE controls", async () => {
  assert.equal((await get("dashboard", "")).status, 401);
  assert.equal(
    (await post("paper-accounts", { name: "test" }, viewer)).status,
    403,
  );
  assert.equal(
    (
      await post(
        "paper-accounts",
        { name: "test" },
        cookie,
        randomUUID(),
        "https://invalid.example",
      )
    ).status,
    403,
  );
  assert.equal(
    (await post("paper-accounts", { name: "test", mode: "LIVE" })).status,
    400,
  );
});
test("persisted isolated lifecycle and concurrent idempotency", async () => {
  const key = randomUUID(),
    body = { name: "TEST " + suffix, initial_balance: "1000.00000001" };
  const [a, b] = await Promise.all([
    post("paper-accounts", body, cookie, key),
    post("paper-accounts", body, cookie, key),
  ]);
  assert.equal(a.status, 200);
  assert.equal(b.status, 200);
  account = (await a.json()).data.id;
  assert.equal((await b.json()).data.id, account);
  assert.equal(
    (await post("paper-accounts", { name: "other" }, cookie, key)).status,
    409,
  );
  for (const [action, expected] of [
    ["start", "STARTING"],
    ["pause", "PAUSED"],
    ["resume", "STARTING"],
    ["stop", "STOPPED"],
  ]) {
    const r = await post("paper-accounts/" + account + "/" + action, {});
    assert.equal((await r.json()).data.status, expected);
  }
  const r = await post("paper-accounts/" + account + "/clone", {});
  clone = (await r.json()).data.id;
  assert.notEqual(clone, account);
  assert.equal(
    (await post("paper-accounts/" + account + "/archive", {})).status,
    200,
  );
  const book = await (await get("ledger?account_id=" + account)).json();
  assert.equal(book.data.length, 1);
  assert.equal(book.data[0].available_after, "1000.00000001");
});
test("collection contracts, filters, OpenAPI, report idempotency", async () => {
  for (const r of Object.keys(resources)) {
    const result = await get(r + "?limit=1");
    assert.equal(result.status, 200, r);
    assert.ok(Array.isArray((await result.json()).data));
  }
  assert.equal((await get("markets?sort=untrusted%20sort")).status, 400);
  assert.equal((await get("openapi.json")).status, 200);
  const range = {
    start_time: "2024-01-01T00:00:00Z",
    end_time: "2024-01-02T00:00:00Z",
  };
  const datasetResponse = await post("backtest-datasets", range);
  assert.equal(datasetResponse.status, 200);
  const dataset = (await datasetResponse.json()).data;
  assert.equal(dataset.status, "DATA_UNAVAILABLE");
  assert.equal(dataset.reason, "NO_MARKETS");
  assert.equal((await get("backtest-datasets?limit=1")).status, 200);
  const unavailableRun = await post("backtests", {
    ...range,
    dataset_id: dataset.id,
  });
  assert.equal(unavailableRun.status, 409);
  assert.equal((await unavailableRun.json()).error.code, "DATA_UNAVAILABLE");
  const report = await (await post("reports", range)).json();
  assert.equal(report.data.result.win_rate, null);
  assert.equal(report.data.result.closing_equity, null);
  const e = await post("experiments", {
    name: "Test",
    hypothesis: "Test",
    criteria: "defined",
    account_ids: [account, clone],
  });
  assert.equal(e.status, 200);
});
