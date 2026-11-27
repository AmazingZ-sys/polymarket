import { after, before, test } from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { Readable } from "node:stream";
import { EventEmitter } from "node:events";
import https from "node:https";
import { app } from "../src/app.js";
import { db, redis } from "../src/db.js";
import { sha } from "../src/security.js";

const prefix = "/api/v1/polymarket-account";
const admin = randomUUID(), viewer = randomUUID();
const adminName = "test-polymarket-" + randomUUID();
const request = (path: string, body?: object, token = admin, key: string = randomUUID()) =>
  app.request(prefix + path, {
    method: body ? "POST" : "GET",
    headers: { cookie: "pq_session=" + token, "Content-Type": "application/json", "Idempotency-Key": key },
    body: body ? JSON.stringify(body) : undefined,
  });
const reset = `UPDATE polymarket_account_settings SET wallet_address=NULL,paper_account_id=NULL,
  strategy_id=NULL,strategy_version=NULL,max_order_usd=NULL,max_total_usd=NULL,enabled=false,
  geoblock_status='UNCHECKED',country=NULL,region=NULL,checked_at=NULL,revision=0,attested_at=NULL,attested_by=NULL WHERE singleton`;

before(async () => {
  if (!process.env.DATABASE_URL?.includes("test")) throw Error("Use a dedicated test database");
  await redis.connect();
  for (const [name, role, token] of [[adminName, "ADMIN", admin], [adminName + "-viewer", "VIEWER", viewer]]) {
    await db.query(`WITH u AS (INSERT INTO users(username,password_hash,role) VALUES($1,'TEST',$2) RETURNING id)
      INSERT INTO user_sessions SELECT $3,id,now()+interval '1 hour' FROM u`, [name, role, sha(token)]);
  }
  await db.query(reset);
});
after(async () => {
  if (process.env.DATABASE_URL?.includes("test")) await db.query(reset);
  await redis.quit();
  await db.end();
});

test("public address configuration is exact, scoped to PAPER and restricted to administrators", async () => {
  assert.equal((await request("", undefined, "")).status, 401);
  assert.equal((await request("", undefined, viewer)).status, 403);
  assert.equal((await request("/disable", {}, viewer)).status, 403);
  const initial = (await (await request("")).json()).data;
  assert.equal(initial.enabled, false);
  assert.equal(initial.config, null);
  assert.equal(initial.geoblock.status, "UNCHECKED");
  const create = async (mode: string) => (await db.query(`SELECT pq_account_command('create',NULL,$1,$2,'test') AS a`,
    [{ name: "TEST " + mode, mode, strategy_id: "S00", config: { strategies: [{ strategy_id: "S01", strategy_version: "1.0.0" }] } }, randomUUID()])).rows[0].a.id;
  const paper = await create("PAPER"), shadow = await create("SHADOW");
  const raw = {
    wallet_address: "0x" + randomUUID().replaceAll("-", "") + "abcde123", paper_account_id: paper, strategy_id: "S01", strategy_version: "1.0.0",
    max_order_usd: "9999999999999.12345678", max_total_usd: "9999999999999.12345679",
  };
  for (const changes of [
    { private_key: "TEST-REJECT-UNEXPECTED-INPUT" }, { wallet_address: "0x" + "b".repeat(64) },
    { wallet_address: "0x" + "0".repeat(40) }, { max_order_usd: 15 }, { max_order_usd: "0" },
    { max_total_usd: "0.000000001" }, { max_order_usd: "9999999999999.12345680" },
  ]) assert.equal((await request("/config", { ...raw, ...changes })).status, 400);
  assert.equal((await request("/config", { ...raw, paper_account_id: shadow })).status, 409);
  assert.equal((await request("/config", { ...raw, strategy_id: "S04" })).status, 409);
  const key = randomUUID();
  const saved = await request("/config", raw, admin, key);
  assert.equal(saved.status, 200);
  assert.deepEqual((await saved.json()).data.config, raw);
  assert.deepEqual((await (await request("/config", raw, admin, key)).json()).data.config, raw);
  const { rows } = await db.query("SELECT details FROM audit_logs WHERE actor=$1 AND resource=$2",
    [adminName, prefix + "/config"]);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].details.max_order_usd, raw.max_order_usd);
  assert.notEqual(rows[0].details.wallet_address, raw.wallet_address);
});

test("direct official geoblock checks and audited enable attempts always fail closed without an executor", async t => {
  let status = 200;
  let body = JSON.stringify({ blocked: false, country: "CH", region: "ZH", ip: "192.0.2.10" });
  let count = 0;
  t.mock.method(https, "get", (url: string, opts: { agent: unknown; headers: object }, callback: (r: unknown) => void) => {
    count++;
    assert.equal(url, "https://polymarket.com/api/geoblock");
    assert.equal(opts.agent, false);
    assert.deepEqual(opts.headers, { Accept: "application/json" });
    const response = Object.assign(Readable.from([Buffer.from(body)]), {
      statusCode: status, headers: { "content-type": "application/json", location: "https://other.invalid" },
    });
    callback(response);
    return new EventEmitter();
  });
  const check = async (expected: string) => {
    const r = await request("/check", {});
    assert.equal(r.status, 200);
    const s = (await r.json()).data;
    assert.equal(s.geoblock.status, expected);
    assert.ok(s.geoblock.checked_at);
    assert.equal(s.enabled, false);
    assert.equal(s.executor_ready, false);
    assert.equal(JSON.stringify(s).includes("192.0.2.10"), false);
  };
  await check("ALLOWED");
  const { data: stored } = await (await request("")).json();
  const enable = { revision: stored.revision, eligibility_confirmed: true };
  assert.equal((await request("/enable", {})).status, 400);
  const key = randomUUID();
  for (let retry = 0; retry < 2; retry++) {
    const r = await request("/enable", enable, admin, key);
    assert.equal(r.status, 409);
    assert.equal((await r.json()).error.code, "ENABLE_BLOCKED");
  }
  assert.equal(count, 2);
  const { rows } = await db.query("SELECT details FROM audit_logs WHERE actor=$1 AND resource=$2", [adminName, prefix + "/enable"]);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].details.geoblock, "ALLOWED");
  assert.equal(rows[0].details.enabled, false);
  body = JSON.stringify({ blocked: true, country: "US", region: "NY", ip: "192.0.2.10" });
  await check("BLOCKED");
  assert.match((await (await request("/enable", enable)).json()).error.message, /限制当前服务出口地区/);
  status = 302;
  await check("UNKNOWN");
  status = 200;
  for (const invalid of ['{"blocked":false}', '{"blocked":"false"}',
    '{"blocked":false,"country":"ZZ","region":"","ip":"192.0.2.10"}', "INVALID", "x".repeat(9000)]) {
    body = invalid;
    await check("UNKNOWN");
  }
  assert.match((await (await request("/enable", enable)).json()).error.message, /无法确认/);
  assert.equal((await (await request("/disable", {})).json()).data.enabled, false);
  assert.equal((await (await request("")).json()).data.geoblock.status, "UNKNOWN");
});

test("verified execution lease is bound to settings revision and enforces a durable shared wallet budget", async t => {
  const response = { blocked: false, ip: "192.0.2.1", country: "CH", region: "ZH" };
  t.mock.method(https, "get", (_u: string, _opts: unknown, cb: (r: unknown) => void) => {
    cb(Object.assign(Readable.from([Buffer.from(JSON.stringify(response))]), {
      statusCode: 200, headers: { "content-type": "application/json" },
    }));
    return new EventEmitter();
  });
  const config = (await (await request("")).json()).data.config;
  const payload = { ...config, max_order_usd: "2.00000001", max_total_usd: "4.00000002" };
  const saved = (await (await request("/config", payload)).json()).data;
  await db.query("UPDATE paper_accounts SET status='RUNNING' WHERE id=$1", [config.paper_account_id]);
  const heartbeat = async (revision: string, when: string = "0 seconds", clobOnly = true) => {
    await db.query(`INSERT INTO polymarket_executor_health(singleton,revision,wallet_address,signer_address,
      auth_expires_at,clob_only,reconciled,geoblock_allowed,funds_ready,healthy,detail_code,checked_at)
      VALUES(true,$1,$2,$3,now()+interval '1 hour',$4,true,true,true,true,'READY',now()-$5::interval)
      ON CONFLICT(singleton) DO UPDATE SET revision=excluded.revision,wallet_address=excluded.wallet_address,
        checked_at=excluded.checked_at,clob_only=excluded.clob_only`,
      [revision, config.wallet_address, "0x" + "12".repeat(20),clobOnly,when]);
  };
  const signal = async () => (await db.query(`INSERT INTO strategy_signals
    (account_id,market_id,event_id,strategy_id,strategy_version,config_hash,action,reason,evidence,created_at)
    VALUES ($1,'TEST-MARKET',$2,$3,$4,'TEST','PAIR','TEST','{}',clock_timestamp()) RETURNING id`,
    [config.paper_account_id, randomUUID(), config.strategy_id, config.strategy_version])).rows[0].id;
  const oldSignal = await signal();
  const enable = {revision: saved.revision, eligibility_confirmed:true};
  await heartbeat(saved.revision, "31 seconds");
  assert.equal((await request("/enable", enable)).status, 409);
  await heartbeat(saved.revision, "0 seconds", false);
  assert.equal((await request("/enable", enable)).status, 409);
  await heartbeat(saved.revision);
  assert.equal((await request("/enable", {...enable, revision:"0"})).status, 409);
  const active = (await (await request("/enable", enable)).json()).data;
  assert.equal(active.enabled, true);
  assert.equal(active.executor_ready, true);
  const reserve = async (id:string, rev=saved.revision, up="2.00000001", down="2.00000001") =>
    (await db.query("SELECT pq_live_reserve($1,$2,$3,$4) ok",[rev,id,up,down])).rows[0].ok;
  const dispatch = async (id:string) =>
    (await db.query("SELECT pq_live_dispatch_ready($1,$2) ok",[saved.revision,id])).rows[0].ok;
  assert.equal(await reserve(oldSignal), false);
  const one=await signal(), two=await signal();
  assert.equal(await reserve(one,saved.revision,"2.00000002"), false);
  const granted = await Promise.all([reserve(one),reserve(two)]);
  assert.equal(granted.filter(Boolean).length, 1);
  const winner=granted[0]?one:two, remaining=granted[0]?two:one;
  assert.equal(await dispatch(winner), true);
  assert.equal(await reserve(winner), false);
  await db.query("SELECT pq_live_result($1,'REJECTED',NULL,NULL)", [winner]);
  assert.equal(await reserve(winner), false);
  assert.equal(await reserve(remaining),true);
  await db.query("SELECT pq_live_result($1,'CONFIRMED','TEST-UP','TEST-DOWN')",[remaining]);
  await db.query("SELECT pq_live_result($1,'CONFIRMED','TEST-UP','TEST-DOWN')",[remaining]);
  await assert.rejects(db.query("SELECT pq_live_result($1,'REJECTED',NULL,NULL)",[remaining]));
  assert.equal(await reserve(await signal()),false);
  const resetConfig = (await (await request("/config",{...payload,max_total_usd:"5"})).json()).data;
  assert.equal(resetConfig.enabled,false);
  assert.equal(resetConfig.committed_usd, "4.00000002");
  assert.equal(resetConfig.executor_ready,false);
  await heartbeat(resetConfig.revision);
  assert.equal((await (await request("/enable", {...enable,revision:resetConfig.revision})).json()).data.enabled,true);
  const uncertain = await signal();
  assert.equal(await reserve(uncertain,resetConfig.revision,"0.5","0.5"),false);
  assert.equal(await reserve(uncertain,resetConfig.revision,"0.25","0.25"),true);
  await db.query("SELECT pq_live_result($1,'UNKNOWN',NULL,NULL)",[uncertain]);
  assert.equal((await (await request("")).json()).data.configured_enabled,false);
  assert.equal((await request("/enable", {...enable,revision:resetConfig.revision})).status,409);
  // Immutable observed spends and fees can arrive separately after the exchange's terminal result.
  await db.query("SELECT pq_live_result($1,'CONFIRMED',NULL,NULL,'1.2','1.5','0.03',NULL,'pUSD',NULL)",[remaining]);
  const partialFee = (await (await request("")).json()).data;
  assert.equal(partialFee.accounting.confirmed_principal_usd, "2.70000000");
  assert.equal(partialFee.accounting.reserved_principal_usd, "0.50000000");
  assert.equal(partialFee.committed_usd, "3.20000000");
  assert.deepEqual(partialFee.accounting.fees, [{currency:"pUSD",amount:"0.03"}]);
  assert.equal(partialFee.accounting.pending_fee_legs, 3);
  const details = partialFee.recent_attempts.find((r: {signal_id:string}) => r.signal_id === remaining);
  assert.equal(details.up_amount,"2.00000001");
  assert.equal(details.up_principal,"1.20000000");
  assert.equal(details.down_fee,null);
  await assert.rejects(db.query("SELECT pq_live_result($1,'CONFIRMED',NULL,NULL,'1.1')",[remaining]));
  await db.query("SELECT pq_live_result($1,'CONFIRMED',NULL,NULL,NULL,NULL,NULL,'0.02',NULL,'USDC')",[remaining]);
  const feeRows = (await (await request("")).json()).data.accounting.fees;
  assert.deepEqual(Object.fromEntries(feeRows.map((r: {currency:string;amount:string}) => [r.currency,r.amount])), {pUSD:"0.03",USDC:"0.02"});
  const stopped=(await (await request("/disable",{})).json()).data;
  assert.equal(stopped.enabled,false);
  assert.equal(stopped.executor_ready,false);
});
