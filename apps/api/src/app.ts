import { Hono } from "hono";
import { streamSSE } from "hono/streaming";
import { bodyLimit } from "hono/body-limit";
import { secureHeaders } from "hono/secure-headers";
import { z } from "zod";
import { ApiError, db, redis } from "./db.js";
import {
  login,
  loginSchema,
  logout,
  protect,
  session,
  type Env,
} from "./security.js";
import { resources, dashboard, list } from "./resources.js";
import { replayWindow } from "./replay.js";
import { commands, invoke } from "./commands.js";

export const app = new Hono<Env>();
app.use("*", secureHeaders(), bodyLimit({ maxSize: 64 * 1024 }));
app.get("/healthz", (c) => c.json({ status: "ok" }));
app.get("/readyz", async (c) => {
  await Promise.all([
    db.query("SELECT 1 FROM paper_accounts LIMIT 0"),
    redis.ping(),
  ]);
  return c.json({ status: "ready" });
});
app.get("/metrics", async (c) => {
  const {
    rows: [m],
  } =
    await db.query(`SELECT (SELECT coalesce(max(seq),0) FROM engine_events) events,
 (SELECT count(*) FROM paper_accounts WHERE mode='PAPER' AND status='RUNNING') accounts,
 (SELECT count(*) FROM data_source_status WHERE status<>'ONLINE') degraded`);
  return c.text(
    "polyquant_last_event_sequence " +
      m.events +
      "\npolyquant_running_accounts " +
      m.accounts +
      "\npolyquant_sources_non_ok " +
      m.degraded +
      "\n",
  );
});
const api = app.basePath("/api/v1");
api.use("*", protect);
api.post("/auth/login", login);
api.post("/auth/logout", logout);
api.get("/auth/me", (c) => c.json({ data: c.get("user") }));
api.get("/dashboard", async (c) => c.json({ data: await dashboard() }));
api.get("/backtests/window", async (c) => c.json({ data: await replayWindow(c.req.query()) }));
api.get("/paper-accounts/:id", async (c) => {
  const { rows } = await db.query("SELECT * FROM paper_accounts WHERE id=$1", [
    z.uuid().parse(c.req.param("id")),
  ]);
  if (!rows.length) throw new ApiError(404, "NOT_FOUND", "账户不存在");
  return c.json({ data: rows[0] });
});
api.get("/events", (c) =>
  streamSSE(c, async (stream) => {
    let previous = "";
    while (!stream.aborted && (await session(c))) {
      const {
        rows: [r],
      } =
        await db.query(`SELECT (SELECT max(seq) FROM engine_events) market,(SELECT max(id) FROM audit_logs) admin,
   (SELECT max(id) FROM equity_snapshots) equity,(SELECT max(id) FROM risk_events) risk,
   (SELECT max(id) FROM paper_sessions) sessions,(SELECT max(created_at) FROM paper_fills) fill,
   (SELECT max(finished_at) FROM backtest_runs) backtest,(SELECT max(last_attempt_at) FROM data_source_status) sources`);
      const next = JSON.stringify(r);
      await stream.writeSSE({
        event: next === previous ? "heartbeat" : "refresh",
        data: next === previous ? "{}" : next,
      });
      previous = next;
      await stream.sleep(5000);
    }
  }),
);
function openapi() {
  const paths: Record<string, Record<string, unknown>> = {};
  function add(path: string, method: string, schema?: z.ZodType) {
    const url = "/api/v1" + path.replace(/:([a-z]+)/g, "{$1}");
    const params: Record<string, unknown>[] = Array.from(
      path.matchAll(/:([a-z]+)/g),
      (m) => ({
        name: m[1],
        in: "path",
        required: true,
        schema: { type: "string" },
      }),
    );
    const collection =
      method === "get" && Object.hasOwn(resources, path.slice(1));
    if (collection) {
      for (const [name, type] of [
        ["limit", "integer"],
        ["offset", "integer"],
        ["sort", "string"],
        ["direction", "string"],
        ["q", "string"],
      ]) {
        params.push({ name, in: "query", schema: { type } });
      }
    }
    if (path === "/backtests/window") {
      for (const [name, description] of [
        ["assets", "逗号分隔的资产 BTC、ETH"],
        ["timeframes", "逗号分隔的市场周期 5、15"],
        ["start_time", "自选区间的起点，与 end_time 同时传入"],
        ["end_time", "自选区间的终点，省略区间时返回最近有观测的窗口或 null"],
      ]) params.push({ name, in: "query", description, schema: { type: "string" } });
    }
    const response = (description: string, name: string) => ({
      description,
      content: {
        "application/json": {
          schema: { $ref: "#/components/schemas/" + name },
        },
      },
    });
    paths[url] ??= {};
    paths[url][method] = {
      responses: {
        "200":
          path === "/events"
            ? {
                description: "事件流",
                content: {
                  "text/event-stream": { schema: { type: "string" } },
                },
              }
            : response("成功", collection ? "Page" : "Result"),
        "400": response("请求无效", "Error"),
        "401": response("需要会话", "Error"),
        "403": response("权限不足", "Error"),
        "409": response("状态冲突", "Error"),
        "429": response("频率超限", "Error"),
        "503": response("依赖暂不可用", "Error"),
      },
      parameters: params,
      ...(schema
        ? {
            requestBody: {
              required: true,
              content: {
                "application/json": {
                  schema: z.toJSONSchema(schema, { io: "input" }),
                },
              },
            },
          }
        : {}),
      ...(method === "post" && !path.startsWith("/auth")
        ? {
            parameters: [
              ...params,
              {
                name: "Idempotency-Key",
                in: "header",
                required: true,
                schema: { type: "string" },
              },
            ],
          }
        : {}),
    };
  }
  for (const r of Object.keys(resources)) add("/" + r, "get");
  for (const r of ["/events", "/dashboard", "/backtests/window", "/auth/me", "/paper-accounts/:id"])
    add(r, "get");
  add("/auth/login", "post", loginSchema);
  add("/auth/logout", "post");
  for (const [path, command] of Object.entries(commands))
    add(path, "post", command.schema);
  return {
    openapi: "3.1.0",
    info: { title: "PolyQuant Pro", version: "0.1.0" },
    paths,
    security: [{ session: [] }],
    components: {
      schemas: {
        Result: {
          type: "object",
          required: ["data"],
          properties: { data: {} },
        },
        Page: {
          type: "object",
          required: ["data", "meta"],
          properties: {
            data: { type: "array", items: { type: "object" } },
            meta: {
              type: "object",
              required: ["limit", "offset", "total"],
              properties: {
                limit: { type: "integer" },
                offset: { type: "integer" },
                total: { type: "integer" },
              },
            },
          },
        },
        Error: {
          type: "object",
          required: ["error"],
          properties: {
            error: {
              type: "object",
              required: ["code", "message"],
              properties: {
                code: { type: "string" },
                message: { type: "string" },
              },
            },
          },
        },
      },
      securitySchemes: {
        session: { type: "apiKey", in: "cookie", name: "pq_session" },
      },
    },
  };
}
api.get("/openapi.json", (c) => c.json(openapi()));
for (const [path, command] of Object.entries(commands))
  api.post(path, (c) => invoke(c, command));
api.get("/:resource", async (c) =>
  c.json(await list(c.req.param("resource"), c.req.query())),
);
app.onError((err, c) => {
  if (err instanceof ApiError)
    return c.json(
      { error: { code: err.code, message: err.message } },
      err.status,
    );
  if (err instanceof z.ZodError || err instanceof SyntaxError)
    return c.json(
      {
        error: {
          code: "INVALID_INPUT",
          message:
            err instanceof z.ZodError
              ? err.issues
                  .map((i) => i.path.join(".") + " " + i.message)
                  .join(", ")
              : "JSON 格式有误",
        },
      },
      400,
    );
  const code = (err as { code?: string }).code ?? "";
  if (
    code === "P0001" ||
    code === "P0002" ||
    code.startsWith("23") ||
    code.startsWith("22")
  )
    return c.json(
      {
        error: {
          code: "BUSINESS_RULE",
          message:
            code === "P0001" ? err.message : "记录、字段或状态不符合业务约束",
        },
      },
      409,
    );
  console.error("request failure", c.req.method, c.req.path, err.name, code);
  return c.json(
    {
      error: { code: "UNAVAILABLE", message: "服务暂时不可用，请检查依赖连接" },
    },
    503,
  );
});
