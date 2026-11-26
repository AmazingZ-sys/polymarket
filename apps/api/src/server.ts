import { serve } from "@hono/node-server";
import { app } from "./app.js";
import { db, redis } from "./db.js";
import { bootstrap } from "./security.js";
await redis.connect();
await bootstrap();
const server = serve({
  fetch: app.fetch,
  port: Number(process.env.PORT ?? 3000),
  hostname: process.env.HOST ?? "0.0.0.0",
});
console.log("PolyQuant management API ready");
for (const signal of ["SIGTERM", "SIGINT"])
  process.once(signal, () => {
    server.close(() =>
      Promise.all([db.end(), redis.quit()]).then(() => process.exit(0)),
    );
    setTimeout(() => process.exit(0), 5000).unref();
  });
