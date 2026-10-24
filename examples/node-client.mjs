// Node.js 20+. Set GEMINI_LOCAL_API_KEY; for LAN also NODE_EXTRA_CA_CERTS.
import { mkdir, rename } from "node:fs/promises";
import { createWriteStream } from "node:fs";
import { Readable } from "node:stream";
import { pipeline } from "node:stream/promises";
import { createHash, randomUUID } from "node:crypto";
import { homedir } from "node:os";
import { join } from "node:path";

const base = process.env.GEMINI_LOCAL_URL || "http://127.0.0.1:8765";
const key = process.env.GEMINI_LOCAL_API_KEY;
if (!key) throw new Error("Set GEMINI_LOCAL_API_KEY first.");
const origin = new URL(base);
if (origin.protocol !== "https:" && !(origin.protocol === "http:" && ["127.0.0.1", "localhost", "[::1]"].includes(origin.hostname))) {
  throw new Error("LAN connections require HTTPS.");
}
const headers = { Authorization: `Bearer ${key}` };
async function request(path, options = {}) {
  const response = await fetch(base + path, {
    ...options, headers: { ...headers, ...options.headers }, redirect: "error",
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}: ${await response.text()}`);
  return response;
}
const job = await (await request("/v1/jobs", {
  method: "POST",
  headers: { "Content-Type": "application/json", "Idempotency-Key": randomUUID() },
  body: JSON.stringify({ prompts: ["A mountain at sunrise"], ratio: "16:9" }),
})).json();
console.log("Job ID:", job.job_id);
const directory = join(homedir(), "Downloads", "GeminiCreatorCore", job.job_id);
await mkdir(directory, { recursive: true });
const saved = new Set();
for (;;) {
  const status = await (await request(`/v1/jobs/${job.job_id}`)).json();
  for (const item of status.prompts) {
    if (item.status !== "completed" || saved.has(item.index)) continue;
    const suffix = { "image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp" }[item.mime];
    if (!suffix) throw new Error("Unknown image type");
    const target = join(directory, String(item.index) + suffix);
    const response = await request(item.download_url);
    const hash = createHash("sha256");
    const body = Readable.fromWeb(response.body);
    body.on("data", chunk => hash.update(chunk));
    await pipeline(body, createWriteStream(target + ".part"));
    if (hash.digest("hex") !== item.sha256) throw new Error("Image checksum mismatch");
    await rename(target + ".part", target);
    saved.add(item.index);
    console.log({ index: item.index, prompt_id: item.id, path: target });
  }
  if (["completed", "partial", "failed", "cancelled", "needs_review"].includes(status.status) &&
      !status.prompts.some(p => ["preparing", "running"].includes(p.status))) {
    console.log("Final status:", status.status);
    break;
  }
  await new Promise(resolve => setTimeout(resolve, 1000));
}
