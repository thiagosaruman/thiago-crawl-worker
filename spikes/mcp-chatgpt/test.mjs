import assert from "node:assert/strict";

const mod = await import("./server.mjs").catch(() => null);
assert.ok(mod?.startSpikeServer, "MCP spike server implementation is missing");

const instance = await mod.startSpikeServer({ port: 0, durationMs: 1200, queuedMs: 150 });
const base = `http://127.0.0.1:${instance.port}`;

async function rpc(id, method, params = undefined) {
  const response = await fetch(`${base}/mcp`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "accept": "application/json, text/event-stream",
      "mcp-protocol-version": "2025-06-18",
    },
    body: JSON.stringify({ jsonrpc: "2.0", id, method, ...(params === undefined ? {} : { params }) }),
  });
  assert.equal(response.status, 200, `${method} should return HTTP 200`);
  return response.json();
}

try {
  const health = await fetch(`${base}/`);
  assert.equal(health.status, 200, "health endpoint should respond");
  const healthBody = await health.json();
  assert.equal(healthBody.service, "crawl-mcp-spike");

  const init = await rpc(1, "initialize", {
    protocolVersion: "2025-06-18",
    capabilities: {},
    clientInfo: { name: "crawl-spike-test", version: "0.0.1" },
  });
  assert.equal(init.result.serverInfo.name, "crawl-mcp-spike");

  const tools = await rpc(2, "tools/list", {});
  assert.deepEqual(
    tools.result.tools.map((tool) => tool.name).sort(),
    ["crawl", "document", "search"],
    "server must expose exactly the three Phase 1 tools",
  );

  const start = await rpc(3, "tools/call", {
    name: "crawl",
    arguments: { operation: "start", query: "Banco Master", depth: 1, refresh: "auto" },
  });
  const started = start.result.structuredContent;
  assert.equal(started.status, "queued");
  assert.ok(started.job_id);
  assert.ok(started.investigation_id);

  const duplicate = await rpc(4, "tools/call", {
    name: "crawl",
    arguments: { operation: "start", query: "Banco Master", depth: 1, refresh: "auto" },
  });
  assert.equal(duplicate.result.structuredContent.job_id, started.job_id, "duplicate start should reuse the same job");
  assert.equal(duplicate.result.structuredContent.reused, true);

  const earlyStatus = await rpc(5, "tools/call", {
    name: "crawl",
    arguments: { operation: "status", job_id: started.job_id },
  });
  assert.ok(["queued", "running"].includes(earlyStatus.result.structuredContent.status));
  assert.equal(earlyStatus.result.structuredContent.job_id, started.job_id);

  await new Promise((resolve) => setTimeout(resolve, 1350));
  const doneStatus = await rpc(6, "tools/call", {
    name: "crawl",
    arguments: { operation: "status", job_id: started.job_id },
  });
  assert.equal(doneStatus.result.structuredContent.status, "completed");

  const search = await rpc(7, "tools/call", {
    name: "search",
    arguments: { query: "Banco Master", investigation_id: started.investigation_id, limit: 10 },
  });
  assert.equal(search.result.structuredContent.total, 1);
  assert.equal(search.result.structuredContent.results[0].document_id, "spike-document-1");
  assert.ok(search.result.structuredContent.results[0].untrusted_content.title);
  assert.equal(search.result.structuredContent.results[0].title, undefined, "web title must not escape untrusted_content");

  const document = await rpc(8, "tools/call", {
    name: "document",
    arguments: { document_id: "spike-document-1", max_chars: 6000 },
  });
  assert.equal(document.result.structuredContent.document_id, "spike-document-1");
  assert.equal(document.result.structuredContent.verification_mode, "excerpt");
  assert.ok(document.result.structuredContent.untrusted_content.text);

  console.log("MCP spike protocol tests passed");
} finally {
  await instance.close();
}
