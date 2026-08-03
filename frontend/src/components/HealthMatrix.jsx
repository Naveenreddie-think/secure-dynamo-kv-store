// Reporting node x peer -> up/down/unreachable. Deliberately a matrix, not
// one boolean per node: gossip state is eventually consistent, so during a
// real partition or a stale/forged relay, different nodes can genuinely
// disagree about who's up -- that disagreement is itself the interesting
// signal (see PROGRESS.md's Phase 7 category 3 finding), not a bug to hide.
export default function HealthMatrix({ results }) {
  const allNodeIds = Array.from(
    new Set(results.flatMap((r) => (r.reachable ? [r.data.node_id, ...Object.keys(r.data.peers)] : [])))
  ).sort();

  if (allNodeIds.length === 0) {
    return <div className="text-slate-400 text-sm">No health data yet.</div>;
  }

  function cellFor(reportingResult, peerId) {
    if (!reportingResult || !reportingResult.reachable) return null;
    if (reportingResult.data.node_id === peerId) return "self";
    return reportingResult.data.peers[peerId] ?? "unknown";
  }

  function badgeClass(status) {
    if (status === "up" || status === "self") return "bg-emerald-500/20 text-emerald-400";
    if (status === "down") return "bg-rose-500/20 text-rose-400";
    return "bg-slate-700 text-slate-400";
  }

  return (
    <table className="text-xs w-full">
      <thead>
        <tr>
          <th className="text-left p-1 text-slate-400">reports \ peer</th>
          {allNodeIds.map((id) => (
            <th key={id} className="p-1 text-slate-400">
              {id}
            </th>
          ))}
          <th className="p-1 text-slate-400">reachable</th>
        </tr>
      </thead>
      <tbody>
        {allNodeIds.map((reportingNodeId) => {
          const result = results.find((r) => r.reachable && r.data.node_id === reportingNodeId);
          return (
            <tr key={reportingNodeId}>
              <td className="p-1 font-medium">{reportingNodeId}</td>
              {allNodeIds.map((peerId) => {
                const status = cellFor(result, peerId);
                return (
                  <td key={peerId} className="p-1 text-center">
                    {status && <span className={`px-2 py-0.5 rounded ${badgeClass(status)}`}>{status}</span>}
                  </td>
                );
              })}
              <td className="p-1 text-center">
                <span className={`px-2 py-0.5 rounded ${badgeClass(result ? "up" : "down")}`}>
                  {result ? "yes" : "no"}
                </span>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
