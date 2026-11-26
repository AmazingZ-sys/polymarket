import pg, { type PoolClient } from "pg";
import { createClient } from "redis";

export const db = new pg.Pool({
  connectionString: process.env.DATABASE_URL,
  max: 12,
  connectionTimeoutMillis: 5000,
});
db.on("error", () => console.error("database connection interrupted"));
export const redis = createClient({
  url: process.env.REDIS_URL ?? "redis://localhost:6379",
  disableOfflineQueue: true,
});
redis.on("error", () => {});
export async function transaction<T>(
  run: (client: PoolClient) => Promise<T>,
): Promise<T> {
  const client = await db.connect();
  try {
    await client.query("BEGIN");
    const result = await run(client);
    await client.query("COMMIT");
    return result;
  } catch (err) {
    await client.query("ROLLBACK");
    throw err;
  } finally {
    client.release();
  }
}
export class ApiError extends Error {
  constructor(
    public status: 400 | 401 | 403 | 404 | 409 | 429 | 503,
    public code: string,
    message: string,
  ) {
    super(message);
  }
}
