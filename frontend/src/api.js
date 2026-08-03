// Polls every known node's /v1/cluster-state and merges client-side --
// deliberately not server-aggregated, so killing one node in the chaos
// panel can't blind the whole dashboard, and a real partition/gossip lag
// shows up honestly as disagreement between nodes rather than being
// smoothed over.
//
// Bootstrapping: on first load we don't yet know the full node roster, so
// we always also poll BOOTSTRAP_URL (this bundle's own origin in
// production; overridable via VITE_BOOTSTRAP_URL for local `npm run dev`
// against a live cluster) and learn public_cluster_urls from whichever
// response(s) succeed.
const BOOTSTRAP_URL = import.meta.env.VITE_BOOTSTRAP_URL || window.location.origin;
export const POLL_INTERVAL_MS = 1500;
const FETCH_TIMEOUT_MS = 2000;

async function fetchWithTimeout(url, timeoutMs) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const resp = await fetch(url, { signal: controller.signal });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return await resp.json();
  } finally {
    clearTimeout(timer);
  }
}

async function fetchClusterState(baseUrl) {
  // A dead origin's fetch doesn't fail fast on its own (TCP/TLS connect
  // timeouts are often 20-30s+) -- without this explicit AbortController
  // deadline, requests to a killed node would pile up across poll cycles
  // instead of being abandoned each tick.
  try {
    const data = await fetchWithTimeout(`${baseUrl}/v1/cluster-state`, FETCH_TIMEOUT_MS);
    return { baseUrl, reachable: true, data };
  } catch (err) {
    return { baseUrl, reachable: false, error: String(err) };
  }
}

export async function pollCluster(knownUrls) {
  const urls = knownUrls.length > 0 ? Array.from(new Set([...knownUrls, BOOTSTRAP_URL])) : [BOOTSTRAP_URL];
  const results = await Promise.all(urls.map(fetchClusterState));

  const discovered = new Set(urls);
  for (const r of results) {
    if (r.reachable) {
      for (const u of r.data.public_cluster_urls || []) discovered.add(u);
    }
  }
  return { results, knownUrls: Array.from(discovered) };
}
