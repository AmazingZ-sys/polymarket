import {
  createHash,
  randomBytes,
  scrypt as derive,
} from "node:crypto";
import { promisify } from "node:util";
import { getCookie, setCookie, deleteCookie } from "hono/cookie";
import { getConnInfo } from "@hono/node-server/conninfo";
import type { Context, MiddlewareHandler } from "hono";
import { z } from "zod";
import { ApiError, db, redis } from "./db.js";

const scrypt = promisify(derive);
export type User = { username: string; role: "ADMIN" | "VIEWER"; id: string };
export type Env = { Variables: { user: User } };
export const sha = (s: string) => createHash("sha256").update(s).digest("hex");
const cookie = "pq_session";
export const loginSchema = z
  .object({
    username: z.string().min(1).max(80),
    password: z.string().min(1).max(256),
  })
  .strict();
export async function bootstrap() {
  for (const [prefix, role] of [
    ["ADMIN", "ADMIN"],
    ["VIEWER", "VIEWER"],
  ]) {
    const password = process.env[prefix + "_PASSWORD"],
      username = process.env[prefix + "_USERNAME"] ?? prefix.toLowerCase();
    if (!password && role === "VIEWER") continue;
    if (!password || password.length < 12 || password.length > 256)
      throw Error(prefix + "_PASSWORD must be 12–256 characters");
    const salt = randomBytes(16).toString("hex");
    const hash = (await scrypt(password, salt, 64)) as Buffer;
    await db.query(
      "INSERT INTO users(username,password_hash,role) VALUES($1,$2,$3) ON CONFLICT(username) DO NOTHING",
      [username, salt + "." + hash.toString("hex"), role],
    );
  }
}
export async function session(c: Context) {
  const token = getCookie(c, cookie);
  if (!token) return null;
  const result = await db.query<User>(
    "SELECT u.id,u.username,u.role FROM user_sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=$1 AND s.expires_at>now()",
    [sha(token)],
  );
  return result.rows[0] ?? null;
}
export const protect: MiddlewareHandler<Env> = async (c, next) => {
  if (c.req.method !== "GET") {
    const origin = c.req.header("origin");
    if (
      origin &&
      origin !== (process.env.PUBLIC_ORIGIN ?? new URL(c.req.url).origin)
    )
      throw new ApiError(403, "ORIGIN", "请求来源未获授权");
    if (
      !c.req
        .header("content-type")
        ?.toLowerCase()
        .startsWith("application/json")
    )
      throw new ApiError(400, "CONTENT_TYPE", "请使用 JSON");
  }
  let address = "local";
  try {
    address = getConnInfo(c).remote.address ?? "local";
  } catch {
    /* in-process contract tests */
  }
  const login = c.req.path.endsWith("/auth/login");
  const bucket =
    "pq:rate:" +
    sha(address) +
    ":" +
    (login ? "login" : "api") +
    ":" +
    Math.floor(Date.now() / 60000);
  const count = await redis.eval(
    "local n=redis.call('incr',KEYS[1]); if n==1 then redis.call('expire',KEYS[1],65) end; return n",
    { keys: [bucket], arguments: [] },
  );
  if (Number(count) > (login ? 12 : 2400))
    throw new ApiError(429, "RATE_LIMIT", "请求过于频繁");
  if (!login) {
    const user = await session(c);
    if (!user) throw new ApiError(401, "UNAUTHENTICATED", "请登录");
    if (
      c.req.method !== "GET" &&
      !c.req.path.endsWith("/auth/logout") &&
      user.role !== "ADMIN"
    )
      throw new ApiError(403, "FORBIDDEN", "需要管理员权限");
    c.set("user", user);
  }
  await next();
};
export async function login(c: Context) {
  const { username, password } = loginSchema.parse(await c.req.json());
  const { rows: [record] } = await db.query("SELECT pq_auth_salt($1) AS salt", [username]);
  const actual = (await scrypt(password, record?.salt ?? "0".repeat(32), 64)) as Buffer;
  const { rows: [user] } = await db.query<User>(
    "SELECT * FROM pq_auth_identity($1,$2)", [username, actual.toString("hex")],
  );
  if (!user)
    throw new ApiError(401, "CREDENTIALS", "用户名或密码错误");
  const token = randomBytes(32).toString("base64url");
  await db.query(
    "INSERT INTO user_sessions VALUES($1,$2,now()+interval '12 hours')",
    [sha(token), user.id],
  );
  setCookie(c, cookie, token, {
    httpOnly: true,
    sameSite: "Strict",
    secure: process.env.COOKIE_SECURE === "true",
    path: "/",
    maxAge: 43200,
  });
  return c.json({ data: { username: user.username, role: user.role } });
}
export async function logout(c: Context) {
  await db.query("DELETE FROM user_sessions WHERE token_hash=$1", [
    sha(getCookie(c, cookie) ?? ""),
  ]);
  deleteCookie(c, cookie, { path: "/" });
  return c.json({ data: { signed_out: true } });
}
