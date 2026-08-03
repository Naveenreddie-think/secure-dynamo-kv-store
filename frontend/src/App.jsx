import { useEffect, useRef, useState } from "react";
import { pollCluster, POLL_INTERVAL_MS } from "./api.js";
import RingVisualization from "./components/RingVisualization.jsx";
import HealthMatrix from "./components/HealthMatrix.jsx";
import OperationLog from "./components/OperationLog.jsx";
import ChaosPanel from "./components/ChaosPanel.jsx";

export default function App() {
  const [knownUrls, setKnownUrls] = useState([]);
  const [results, setResults] = useState([]);
  const knownUrlsRef = useRef(knownUrls);
  knownUrlsRef.current = knownUrls;

  useEffect(() => {
    let cancelled = false;

    async function tick() {
      const { results: r, knownUrls: u } = await pollCluster(knownUrlsRef.current);
      if (cancelled) return;
      setResults(r);
      setKnownUrls(u);
    }

    tick();
    const interval = setInterval(tick, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  const firstReachable = results.find((r) => r.reachable);
  const ring = firstReachable?.data.ring;
  const nodeIds = Array.from(
    new Set(results.flatMap((r) => (r.reachable ? [r.data.node_id, ...Object.keys(r.data.peers)] : [])))
  ).sort();

  return (
    <div className="min-h-screen p-6 max-w-6xl mx-auto space-y-6">
      <header>
        <h1 className="text-2xl font-bold">dynamokv cluster dashboard</h1>
        <p className="text-slate-400 text-sm">
          Polling {knownUrls.length || 1} node{knownUrls.length === 1 ? "" : "s"} every {POLL_INTERVAL_MS / 1000}s
        </p>
      </header>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <section className="bg-slate-900/50 rounded-lg p-4">
          <h2 className="font-semibold mb-2">Hash ring</h2>
          <RingVisualization ring={ring} />
        </section>

        <section className="bg-slate-900/50 rounded-lg p-4">
          <h2 className="font-semibold mb-2">Node health (per reporting node's own gossip view)</h2>
          <HealthMatrix results={results} />
        </section>

        <section className="bg-slate-900/50 rounded-lg p-4">
          <h2 className="font-semibold mb-2">Recent operations</h2>
          <OperationLog results={results} />
        </section>

        <section className="bg-slate-900/50 rounded-lg p-4">
          <h2 className="font-semibold mb-2">Chaos panel (advisory -- run these yourself)</h2>
          <ChaosPanel nodeIds={nodeIds} />
        </section>
      </div>
    </div>
  );
}
