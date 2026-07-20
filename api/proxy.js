/**
 * Vercel serverless proxy — forwards /api/* requests to the Azure VM backend.
 * This avoids mixed-content blocking: browser → Vercel (HTTPS) → VM (HTTP).
 */

const VM_BASE = process.env.VM_BASE_URL || "http://photo-sorter-vm.westus2.cloudapp.azure.com:8000";

export default async function handler(req, res) {
  // req.url is e.g. "/api/years" or "/api/thumb?path=..."
  const target = VM_BASE + req.url;

  const headers = {};
  if (req.headers["x-api-key"]) headers["X-Api-Key"] = req.headers["x-api-key"];

  let vmResp;
  try {
    vmResp = await fetch(target, { headers, signal: AbortSignal.timeout(55000) });
  } catch (err) {
    res.status(502).json({ error: "VM unreachable", detail: err.message });
    return;
  }

  const contentType = vmResp.headers.get("content-type") || "application/json";
  res.status(vmResp.status);
  res.setHeader("content-type", contentType);

  // Stream binary (thumbnails) vs text (JSON)
  if (contentType.startsWith("image/")) {
    const buf = Buffer.from(await vmResp.arrayBuffer());
    res.end(buf);
  } else {
    res.end(await vmResp.text());
  }
}
