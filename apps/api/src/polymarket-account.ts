import https from "node:https";
import { isIP } from "node:net";
import { z } from "zod";
import { type PoolClient } from "pg";
import { ApiError, db } from "./db.js";
import type { Command } from "./commands.js";

const geoUrl = "https://polymarket.com/api/geoblock";
type Geo = { status: "UNCHECKED" | "ALLOWED" | "BLOCKED" | "UNKNOWN"; country: string | null; region: string | null };
const unknown: Geo = { status: "UNKNOWN", country: null, region: null };
const regionNames = new Intl.DisplayNames(["en"], { type: "region", fallback: "none" });
const countryCode = z.string().regex(/^[A-Z]{2}$/).refine(c => c !== "ZZ" && c !== "EU" && !!regionNames.of(c));
const geoResponse = z.object({
  blocked: z.boolean(), ip: z.string().refine(ip => isIP(ip) !== 0),
  country: countryCode, region: z.string().max(12).regex(/^[A-Za-z0-9-]*$/),
});

export function checkGeoblock(): Promise<Geo> {
  // Direct HTTPS GET to the official origin. No user input, proxy config,
  // browser headers, forwarded IP address or automatic redirects.
  return new Promise(resolve => {
    const req = https.get(geoUrl, {
      agent: false, signal: AbortSignal.timeout(5000), headers: { Accept: "application/json" },
    }, res => {
      res.on("error", () => resolve(unknown));
      if (res.statusCode !== 200 || !/^application\/json\b/i.test(res.headers["content-type"] ?? "")) {
        res.destroy();
        return resolve(unknown);
      }
      const chunks: Buffer[] = [];
      let bytes = 0;
      res.on("data", (chunk: Buffer) => {
        bytes += chunk.length;
        if (bytes > 8192) {
          res.destroy();
          resolve(unknown);
        } else chunks.push(chunk);
      });
      res.on("end", () => {
        try {
          const g = geoResponse.parse(JSON.parse(Buffer.concat(chunks).toString("utf8")));
          resolve({ status: g.blocked ? "BLOCKED" : "ALLOWED", country: g.country, region: g.region || null });
        } catch { resolve(unknown); }
      });
    });
    req.on("error", () => resolve(unknown));
  });
}

const amount = z.string().regex(/^(?:0|[1-9]\d{0,12})(?:\.\d{1,8})?$/).refine(v => /[1-9]/.test(v));
const settings = z.object({
  wallet_address: z.string().regex(/^0x[0-9a-fA-F]{40}$/).refine(v => !/^0x0{40}$/.test(v)),
  paper_account_id: z.uuid(),
  strategy_id: z.enum(["S00", "S01", "S02", "S03", "S04"]),
  strategy_version: z.string().regex(/^\d+\.\d+\.\d+$/),
  max_order_usd: amount, max_total_usd: amount,
}).strict();
const empty = z.object({}).strict();

async function state(client: Pick<PoolClient, "query">) {
  const { rows: [row] } = await client.query(`SELECT s.*, committed_usd >= max_total_usd AS exhausted,
    committed_usd-confirmed_principal_usd AS reserved_principal_usd,
    (SELECT coalesce(jsonb_agg(jsonb_build_object('currency',currency,'amount',amount::text) ORDER BY currency),'[]')
      FROM (SELECT currency,sum(fee) AS amount FROM polymarket_live_attempts a
        CROSS JOIN LATERAL (VALUES (up_fee_currency,up_fee),(down_fee_currency,down_fee)) AS legs(currency,fee)
        WHERE lower(a.wallet_address)=lower(s.wallet_address) AND fee IS NOT NULL GROUP BY currency) totals) AS fees,
    (SELECT coalesce(jsonb_agg(to_jsonb(a) ORDER BY created_at DESC,signal_id::bigint DESC),'[]') FROM (
      SELECT signal_id::text,state,created_at,updated_at,up_amount::text,down_amount::text,up_principal::text,
        down_principal::text,up_fee::text,down_fee::text,up_fee_currency,down_fee_currency,up_order_id,down_order_id
        FROM polymarket_live_attempts WHERE lower(wallet_address)=lower(s.wallet_address)
        ORDER BY created_at DESC,polymarket_live_attempts.signal_id DESC LIMIT 20) a) AS recent_attempts
    FROM polymarket_execution_status s WHERE singleton`);
  const config = row.wallet_address ? {
    wallet_address: row.wallet_address, paper_account_id: row.paper_account_id, strategy_id: row.strategy_id,
    strategy_version: row.strategy_version, max_order_usd: row.max_order_usd, max_total_usd: row.max_total_usd,
  } : null;
  const blockers = [];
  if (!config) blockers.push("请先保存钱包地址、模拟账户、策略和授权预算。");
  if (config && config.strategy_id !== "S01") blockers.push("当前执行器仅支持 S01 双腿策略的实时信号。");
  if (row.geoblock_status === "BLOCKED") blockers.push("Polymarket 限制当前服务出口地区。");
  if (row.geoblock_status === "UNCHECKED") blockers.push("尚未检查服务出口地区。");
  if (row.geoblock_status === "UNKNOWN") blockers.push("无法确认当前服务出口地区是否允许交易。");
  if (!row.executor_ready) blockers.push("执行器的心跳、CLOB 授权、余额或对账尚未通过当前配置检查。");
  if (config && !row.paper_ready) blockers.push("选定的 PAPER 账户尚未运行。");
  if (row.halted) blockers.push("全局暂停生效。");
  if (Number(row.unresolved) > 0) blockers.push("钱包有待核对的实盘请求。");
  if (row.exhausted) blockers.push("累计授权预算已用完。");
  return { enabled: row.enabled && !blockers.length, configured_enabled: row.enabled,
    revision: row.revision, enabled_at: row.enabled_at, committed_usd: row.committed_usd,
    recent_attempts: row.recent_attempts, accounting: {
      confirmed_principal_usd: row.confirmed_principal_usd, reserved_principal_usd: row.reserved_principal_usd,
      fees: row.fees, pending_fee_legs: Number(row.pending_fee_legs),
    },
    config, executor_ready: row.executor_ready, executor_checked_at: row.heartbeat_at, executor_detail_code: row.detail_code,
    authorization_expires_at: row.auth_expires_at, blockers, geoblock: {
    status: row.geoblock_status, country: row.country, region: row.region, checked_at: row.checked_at,
  } };
}

export const polymarketAccountStatus = () => state(db);

async function check(tx: PoolClient) {
  await tx.query("SELECT singleton FROM polymarket_account_settings WHERE singleton FOR UPDATE");
  const geo = await checkGeoblock();
  await tx.query(`UPDATE polymarket_account_settings SET geoblock_status=$1,country=$2,region=$3,
    checked_at=clock_timestamp(),enabled=(enabled AND $1='ALLOWED') WHERE singleton`, [geo.status, geo.country, geo.region]);
  return state(tx);
}

export const polymarketAccountCommands: Record<string, Command> = {
  "/polymarket-account/config": {
    schema: settings,
    run: async (tx, p) => {
      const { rows: [budget] } = await tx.query("SELECT $1::numeric <= $2::numeric AS valid", [p.max_order_usd, p.max_total_usd]);
      if (!budget.valid) throw new ApiError(400, "BUDGET", "单笔授权预算不能超过累计授权预算。");
      const { rowCount } = await tx.query(`UPDATE polymarket_account_settings s
        SET wallet_address=$1,paper_account_id=a.id,strategy_id=$3,strategy_version=$4,
            max_order_usd=$5,max_total_usd=$6,enabled=false,revision=s.revision+1,
            attested_at=NULL,attested_by=NULL,enabled_at=NULL,enabled_after_signal=0
        FROM paper_accounts a WHERE s.singleton AND a.id=$2 AND a.mode='PAPER'
          AND ((a.strategy_id=$3 AND a.strategy_version=$4) OR
               a.config->'strategies' @> jsonb_build_array(jsonb_build_object('strategy_id',$3::text,'strategy_version',$4::text)))`,
        [p.wallet_address,p.paper_account_id,p.strategy_id,p.strategy_version,p.max_order_usd,p.max_total_usd]);
      if (!rowCount) throw new ApiError(409, "PAPER_STRATEGY", "请选择模拟账户已登记的策略及版本。");
      return state(tx);
    },
    auditDetails: p => ({ ...p, wallet_address: String(p.wallet_address).slice(0, 6) + "…" + String(p.wallet_address).slice(-4) }),
  },
  "/polymarket-account/check": { schema: empty, run: check },
  "/polymarket-account/enable": {
    schema: z.object({ revision: z.string().regex(/^[0-9]{1,19}$/), eligibility_confirmed: z.literal(true) }).strict(),
    run: async (tx, p, c) => {
      const checked = await check(tx);
      if (checked.revision !== p.revision)
        throw new ApiError(409, "STALE_CONFIG", "账号配置已变化，请重新核对钱包和预算。");
      if (!checked.blockers.length && !checked.configured_enabled) {
        await tx.query(`UPDATE polymarket_account_settings SET enabled=true,enabled_at=clock_timestamp(),
          enabled_after_signal=(SELECT coalesce(max(id),0) FROM strategy_signals),attested_at=clock_timestamp(),
          attested_by=$1 WHERE singleton`, [c.get("user").username]);
      }
      return state(tx);
    },
    auditDetails: (p, result) => {
      const s = result as Awaited<ReturnType<typeof state>>;
      return { ...p, enabled: s.enabled, geoblock: s.geoblock.status, executor_ready: s.executor_ready, blockers: s.blockers };
    },
    afterCommit: async result => {
      const s = result as Awaited<ReturnType<typeof state>>;
      if (!s.enabled) throw new ApiError(409, "ENABLE_BLOCKED", s.blockers.join(" "));
      const current = await state(db);
      if (current.revision !== s.revision || !current.enabled)
        throw new ApiError(409, "ENABLE_BLOCKED", current.blockers.join(" ") || "当前配置已停用或变更。");
      return current;
    },
  },
  "/polymarket-account/disable": {
    schema: empty,
    run: async tx => {
      await tx.query(`UPDATE polymarket_account_settings SET enabled=false,revision=revision+1,enabled_at=NULL,
        attested_at=NULL,attested_by=NULL,enabled_after_signal=0 WHERE singleton`);
      return state(tx);
    },
  },
};
