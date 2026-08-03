// Merged across every reachable node's own recent_ops feed, sorted by
// timestamp. No de-duplication needed: each client operation is
// coordinated and logged by exactly one node (create_public_app() never
// mounts internal_router, so replica fan-out never reaches this feed).
export default function OperationLog({ results, limit = 50 }) {
  const ops = results
    .filter((r) => r.reachable)
    .flatMap((r) => r.data.recent_ops)
    .sort((a, b) => b.timestamp - a.timestamp)
    .slice(0, limit);

  if (ops.length === 0) {
    return <div className="text-slate-400 text-sm">No operations recorded yet.</div>;
  }

  return (
    <div className="max-h-80 overflow-y-auto text-xs font-mono">
      {ops.map((op, i) => (
        <div
          key={`${op.node_id}-${op.timestamp}-${i}`}
          className={`flex gap-2 py-0.5 border-b border-slate-800 ${
            op.conflict ? "bg-amber-500/10 text-amber-300" : ""
          }`}
        >
          <span className="text-slate-500">{new Date(op.timestamp * 1000).toLocaleTimeString()}</span>
          <span className="text-slate-400">{op.node_id}</span>
          <span className="font-semibold">{op.method}</span>
          <span className="truncate">{op.key}</span>
          <span className={op.status_code >= 400 ? "text-rose-400" : "text-emerald-400"}>{op.status_code}</span>
          <span className="text-slate-500">{op.latency_ms.toFixed(1)}ms</span>
          {op.conflict && <span className="font-semibold">CONFLICT</span>}
        </div>
      ))}
    </div>
  );
}
