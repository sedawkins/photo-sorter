/**
 * Vercel serverless proxy — forwards /api/* requests to the Azure VM backend.
 * This avoids mixed-content blocking: browser → Vercel (HTTPS) → VM (HTTP).
 *
 * Adds X-Request-ID to every outbound request so VM logs and Vercel logs
 * share a common trace ID. The VM echoes it back as X-Trace-ID.
 */

const VM_BASE = process.env.VM_BASE_URL || "http://photo-sorter-vm.westus2.cloudapp.azure.com:8000";

function shortId() {
  return Math.random().toString(36).slice(2, 10) +
         Math.random().toString(36).slice(2, 6);
}

export default async function handler(req, res) {
  const traceId = shortId();
  const target = VM_BASE + req.url;

  console.log(`[${traceId}] → ${req.method} ${req.url}`);

  const headers = { "X-Request-ID": traceId };
  if (req.headers["x-api-key"])    headers["X-Api-Key"]     = req.headers["x-api-key"];
  if (req.headers["content-type"]) headers["content-type"]  = req.headers["content-type"];

  const fetchOpts = { method: req.method, headers, signal: AbortSignal.timeout(55000) };
  if (req.method !== "GET" && req.method !== "HEAD" && req.body) {
    fetchOpts.body = JSON.stringify(req.body);
  }

  let vmResp;
  try {
    vmResp = await fetch(target, fetchOpts);
  } catch (err) {
    console.error(`[${traceId}] VM unreachable: ${err.message}`);
    res.status(502).json({ error: "VM unreachable", detail: err.message, trace_id: traceId });
    return;
  }

  const vmTrace = vmResp.headers.get("x-trace-id") || traceId;
  console.log(`[${traceId}] ← ${vmResp.status} (vm trace: ${vmTrace})`);

  const contentType = vmResp.headers.get("content-type") || "application/json";
  res.status(vmResp.status);
  res.setHeader("content-type", contentType);
  res.setHeader("X-Trace-ID", vmTrace);

  if (contentType.startsWith("image/")) {
    const buf = Buffer.from(await vmResp.arrayBuffer());
    res.end(buf);
  } else {
    res.end(await vmResp.text());
  }
}
