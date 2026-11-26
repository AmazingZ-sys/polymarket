import type { Context } from "hono";
import type { PoolClient } from "pg";
import { z } from "zod";
import { transaction, ApiError } from "./db.js";
import { sha, type Env } from "./security.js";

const id = z.uuid(),
  name = z.string().trim().min(1).max(120),
  note = z.string().trim().min(1).max(4000);
const strategy = z.enum(["S00", "S01", "S02", "S03", "S04"]),
  version = z.string().regex(/^\d+\.\d+\.\d+$/);
const amount = z.string().regex(/^(?:0|[1-9]\d{0,12})(?:\.\d{1,8})?$/);
const ratio = z.union([
  z.number(),
  z.string().regex(/^0(?:\.\d+)?$|^1(?:\.0+)?$/),
]);
const config = z
  .object({
    strategies: z
      .array(
        z.object({ strategy_id: strategy, strategy_version: version }).strict(),
      )
      .max(4)
      .optional(),
    minimum_edge: ratio.optional(),
    cost_multiplier: z.coerce.number().min(1).max(3).optional(),
    latency_multiplier: z.coerce.number().min(1).max(5).optional(),
    assets: z
      .array(z.enum(["BTC", "ETH"]))
      .min(1)
      .max(2)
      .optional(),
    timeframes: z
      .array(z.union([z.literal(5), z.literal(15)]))
      .min(1)
      .max(2)
      .optional(),
    simulation_model: z
      .enum(["basic-v1", "conservative-v1", "stress-v1"])
      .optional(),
    tail_seconds: z.number().int().positive().max(900).optional(),
    hypothesis: note.optional(),
    risk_per_trade: ratio.optional(),
    daily_loss: ratio.optional(),
    max_drawdown: ratio.optional(),
    market_cap: ratio.optional(),
    asset_cap: ratio.optional(),
    portfolio_cap: ratio.optional(),
  })
  .strict();
const account = z
  .object({
    name,
    initial_balance: amount.optional(),
    mode: z.enum(["PAPER", "SHADOW"]).optional(),
    strategy_id: strategy.optional(),
    strategy_version: version.optional(),
    config: config.optional(),
  })
  .strict();
const period = z.object({
  start_time: z.iso.datetime({ offset: true }),
  end_time: z.iso.datetime({ offset: true }),
});
const datasetRequest = period
  .extend({
    assets: z.array(z.enum(["BTC", "ETH"])).min(1).max(2).default(["BTC", "ETH"]),
    timeframes: z.array(z.union([z.literal(5), z.literal(15)])).min(1).max(2).default([5, 15]),
    max_bytes: z.number().int().min(1048576).max(1073741824).default(536870912),
    expires_at: z.iso.datetime({ offset: true }).optional(),
  })
  .strict();
const textId = (c: Context) => id.parse(c.req.param("id"));
const singleton = async (tx: PoolClient, sql: string, values: unknown[]) => {
  const { rows } = await tx.query(sql, values);
  if (!rows.length)
    throw new ApiError(409, "CONFLICT", "当前记录或状态不允许此操作");
  return rows[0];
};
type Command = {
  schema: z.ZodType;
  run: (
    tx: PoolClient,
    input: Record<string, unknown>,
    c: Context<Env>,
    key: string,
  ) => Promise<unknown>;
};
async function changeAccount(
  tx: PoolClient,
  c: Context<Env>,
  act: string,
  accountId: string | null,
  body: Record<string, unknown>,
  key: string,
) {
  if (accountId)
    await singleton(
      tx,
      "SELECT id FROM paper_accounts WHERE id=$1 AND mode IN ('PAPER','SHADOW') FOR UPDATE",
      [accountId],
    );
  if (["start", "resume"].includes(act)) {
    const {
      rows: [flag],
    } = await tx.query(
      "SELECT value FROM system_settings WHERE name='global_halt' FOR SHARE",
    );
    if (flag.value) throw new ApiError(409, "HALTED", "请先人工解除全局暂停");
  }
  return (
    await singleton(tx, "SELECT pq_account_command($1,$2,$3,$4,$5) AS data", [
      act,
      accountId,
      body,
      key,
      c.get("user").username,
    ])
  ).data;
}
export const commands: Record<string, Command> = {
  "/paper-accounts": {
    schema: account,
    run: (tx, p, c, k) => changeAccount(tx, c, "create", null, p, k),
  },
  "/paper-accounts/:id/:action": {
    schema: z.object({ name: name.optional() }).strict(),
    run: (tx, p, c, k) =>
      changeAccount(
        tx,
        c,
        z
          .enum(["start", "pause", "resume", "stop", "clone", "archive"])
          .parse(c.req.param("action")),
        textId(c),
        p,
        k,
      ),
  },
  "/backtests": {
    schema: period
      .extend({
        strategy_id: strategy.default("S00"),
        strategy_version: version.default("1.0.0"),
        initial_balance: amount.default("1000"),
        config: config.optional(),
        seed: z.number().int().default(0),
        dataset_id: id.optional(),
      })
      .strict(),
    run: async (tx, p) => {
      if (
        Date.parse(String(p.end_time)) <= Date.parse(String(p.start_time)) ||
        Date.parse(String(p.end_time)) > Date.now()
      )
        throw new ApiError(400, "PERIOD", "请选择已结束的有效时间段");
      if (p.dataset_id) {
        const dataset = await singleton(
          tx,
          "SELECT status,start_time,end_time,assets,timeframes,expires_at<=now() AS expired FROM backtest_datasets WHERE id=$1 FOR SHARE",
          [p.dataset_id],
        );
        if (dataset.expired)
          throw new ApiError(409, "DATA_UNAVAILABLE", "数据集请求已过期");
        if (dataset.status !== "BACKTEST_READY")
          throw new ApiError(409, dataset.status, `数据集状态为 ${dataset.status}`);
        if (
          Date.parse(String(dataset.start_time)) !== Date.parse(String(p.start_time)) ||
          Date.parse(String(dataset.end_time)) !== Date.parse(String(p.end_time))
        )
          throw new ApiError(400, "DATASET_PERIOD", "回测区间必须与数据集请求一致");
        const options = (p.config ?? {}) as z.infer<typeof config>;
        if (options.assets?.some((a) => !dataset.assets.includes(a)) ||
            options.timeframes?.some((t) => !dataset.timeframes.includes(t)))
          throw new ApiError(400, "DATASET_SCOPE", "策略范围超出已检查的数据集");
        p.config = {
          ...options,
          assets: options.assets ?? dataset.assets,
          timeframes: options.timeframes ?? dataset.timeframes,
        };
      }
      return singleton(
        tx,
        "INSERT INTO backtest_runs(config,dataset_id) VALUES($1,$2) RETURNING *",
        [p, p.dataset_id ?? null],
      );
    },
  },
  "/backtest-datasets": {
    schema: datasetRequest,
    run: async (tx, p, c) => {
      if (Date.parse(String(p.start_time)) >= Date.parse(String(p.end_time)) ||
          Date.parse(String(p.end_time)) > Date.now())
        throw new ApiError(400, "PERIOD", "请选择已结束的有效时间段");
      const row = await singleton(
        tx,
        `INSERT INTO backtest_datasets(assets,timeframes,start_time,end_time,max_bytes,expires_at,requested_by)
         VALUES(array(SELECT DISTINCT v FROM unnest($1::text[]) AS v ORDER BY v),
                array(SELECT DISTINCT v FROM unnest($2::integer[]) AS v ORDER BY v),
                $3,$4,$5,coalesce($6::timestamptz,now()+interval '7 days'),$7)
         ON CONFLICT(assets,timeframes,start_time,end_time) DO UPDATE
           SET max_bytes=excluded.max_bytes,expires_at=excluded.expires_at,requested_by=excluded.requested_by
         RETURNING id`,
        [p.assets, p.timeframes, p.start_time, p.end_time, p.max_bytes, p.expires_at ?? null, c.get("user").username],
      );
      return (
        await singleton(tx, "SELECT pq_refresh_backtest_dataset($1) AS data", [row.id])
      ).data;
    },
  },
  "/backtest-datasets/:id/refresh": {
    schema: z.object({}).strict(),
    run: async (tx, _p, c) =>
      (
        await singleton(tx, "SELECT pq_refresh_backtest_dataset($1) AS data", [textId(c)])
      ).data,
  },
  "/backtests/:id/cancel": {
    schema: z.object({}).strict(),
    run: (tx, _p, c) =>
      singleton(
        tx,
        "UPDATE backtest_runs SET status='CANCELLED',finished_at=now() WHERE id=$1 AND status IN ('QUEUED','RUNNING') RETURNING *",
        [textId(c)],
      ),
  },
  "/experiments": {
    schema: z
      .object({
        name,
        hypothesis: note,
        account_ids: z.array(id).min(2).max(20),
        criteria: note,
      })
      .strict(),
    run: async (tx, p) => {
      const rows = (
        await tx.query(
          "SELECT id,strategy_id,strategy_version,initial_balance,config_hash,config FROM paper_accounts WHERE id=ANY($1) AND mode='PAPER' ORDER BY id FOR SHARE",
          [p.account_ids],
        )
      ).rows;
      if (rows.length !== (p.account_ids as string[]).length)
        throw new ApiError(400, "ACCOUNTS", "请选择不同的有效模拟账户");
      return singleton(
        tx,
        "INSERT INTO comparison_experiments(name,hypothesis,account_ids,criteria,snapshot) VALUES($1,$2,$3,$4,$5) RETURNING *",
        [p.name, p.hypothesis, p.account_ids, p.criteria, JSON.stringify(rows)],
      );
    },
  },
  "/strategy-versions": {
    schema: z
      .object({
        strategy_id: strategy,
        version,
        config,
        description: note,
        base_version: version.default("1.0.0"),
      })
      .strict(),
    run: (tx, p) =>
      singleton(
        tx,
        "INSERT INTO strategy_versions(strategy_id,version,parameters,implementation_hash) SELECT strategy_id,$2,parameters||$3::jsonb,implementation_hash FROM strategy_versions WHERE strategy_id=$1 AND version=$4 RETURNING *",
        [p.strategy_id, p.version, p.config, p.base_version],
      ),
  },
  "/improvements": {
    schema: z
      .object({
        strategy_id: strategy,
        problem: note,
        evidence: note,
        hypothesis: note,
        acceptance: note,
        details: z.record(z.string(), z.unknown()).optional(),
      })
      .strict(),
    run: (tx, p) =>
      singleton(
        tx,
        "INSERT INTO improvement_proposals(strategy_id,problem,evidence,hypothesis,acceptance,details) VALUES($1,$2,$3,$4,$5,$6) RETURNING *",
        [
          p.strategy_id,
          p.problem,
          p.evidence,
          p.hypothesis,
          p.acceptance,
          p.details ?? {},
        ],
      ),
  },
  "/improvements/:id/review": {
    schema: z
      .object({ status: z.enum(["APPROVED", "REJECTED"]), reason: note })
      .strict(),
    run: (tx, p, c) =>
      singleton(
        tx,
        "UPDATE improvement_proposals SET status=$2,reason=$3,reviewed_at=now() WHERE id=$1 AND status='PENDING' RETURNING *",
        [textId(c), p.status, p.reason],
      ),
  },
  "/reports": {
    schema: period.extend({ account_id: id.optional() }).strict(),
    run: async (tx, p) =>
      (
        await singleton(tx, "SELECT pq_report($1,$2,$3,'CUSTOM') data", [
          p.account_id ?? null,
          p.start_time,
          p.end_time,
        ])
      ).data,
  },
  "/evaluations": {
    schema: z
      .object({
        experiment_id: id,
        account_id: id,
        backtest_id: id,
        stress_backtest_id: id,
        cost_usd: amount,
        checks: z
          .object({
            quality: z.boolean(),
            delay: z.boolean(),
            extreme: z.boolean(),
            multiple_testing: z.boolean(),
            independent_sample: z.boolean(),
            continuous_coverage: z.boolean(),
          })
          .strict(),
        evidence: note,
      })
      .strict(),
    run: async (tx, p, c) =>
      (
        await singleton(tx, "SELECT pq_evaluate($1,$2) data", [
          p,
          c.get("user").username,
        ])
      ).data,
  },
  "/strategy-versions/:strategy/:version/promote": {
    schema: z.object({ evaluation_id: id, reason: note }).strict(),
    run: async (tx, p, c) =>
      (
        await singleton(tx, "SELECT pq_promote($1,$2,$3,$4,$5) data", [
          strategy.parse(c.req.param("strategy")),
          version.parse(c.req.param("version")),
          p.evaluation_id,
          c.get("user").username,
          p.reason,
        ])
      ).data,
  },
  "/risk/halt": {
    schema: z.object({}).strict(),
    run: async (tx, _p, c, k) => {
      await tx.query(
        "UPDATE system_settings SET value='true',updated_at=now() WHERE name='global_halt'",
      );
      const { rows } = await tx.query(
        "SELECT id FROM paper_accounts WHERE mode<>'BACKTEST' AND status IN ('RUNNING','STARTING') ORDER BY id FOR UPDATE",
      );
      for (const { id } of rows)
        await changeAccount(tx, c, "pause", id, {}, k + "/" + id);
      return { paused: rows.length };
    },
  },
  "/risk/release": {
    schema: z.object({ reason: note }).strict(),
    run: async (tx) =>
      singleton(
        tx,
        "UPDATE system_settings SET value='false',updated_at=now() WHERE name='global_halt' RETURNING *",
        [],
      ),
  },
  "/jobs/:name/trigger": {
    schema: z.object({}).strict(),
    run: (tx, _p, c) =>
      singleton(
        tx,
        "UPDATE scheduled_jobs SET next_run_at=now() WHERE name=$1 RETURNING *",
        [name.parse(c.req.param("name"))],
      ),
  },
};
export async function invoke(c: Context<Env>, command: Command) {
  const key = z.string().min(8).max(100).parse(c.req.header("Idempotency-Key"));
  const body = command.schema.parse(await c.req.json()) as Record<
    string,
    unknown
  >;
  const actor = c.get("user").username,
    path = c.req.path,
    fingerprint = sha(JSON.stringify([path, body]));
  const data = await transaction(async (tx) => {
    await tx.query("SELECT pg_advisory_xact_lock(hashtextextended($1,1))", [
      actor + "/" + key,
    ]);
    const {
      rows: [cached],
    } = await tx.query("SELECT * FROM api_commands WHERE actor=$1 AND key=$2", [
      actor,
      key,
    ]);
    if (cached) {
      if (cached.fingerprint !== fingerprint)
        throw new ApiError(
          409,
          "IDEMPOTENCY_CONFLICT",
          "此幂等键已对应其他操作",
        );
      return cached.result;
    }
    const result = await command.run(tx, body, c, key);
    await tx.query(
      "INSERT INTO api_commands(actor,key,fingerprint,result) VALUES($1,$2,$3,$4)",
      [actor, key, fingerprint, result],
    );
    await tx.query(
      "INSERT INTO audit_logs(actor,action,resource,details) VALUES($1,$2,$3,$4)",
      [actor, "POST", path, body],
    );
    return result;
  });
  return c.json({ data });
}
