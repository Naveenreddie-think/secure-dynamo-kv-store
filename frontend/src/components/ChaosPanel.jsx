// Advisory only, by design: a browser can't run Docker commands, and
// giving a network-reachable endpoint the ability to control the Docker
// host would be real new attack surface for a security-focused project to
// take on lightly. This panel shows the exact command to run yourself --
// the ring/health/log views above will still update live once you do.
function CommandRow({ label, command }) {
  return (
    <div className="flex items-center justify-between gap-2 py-1">
      <span className="text-slate-400 w-24 shrink-0">{label}</span>
      <code className="flex-1 bg-slate-900 rounded px-2 py-1 text-xs truncate">{command}</code>
      <button
        className="text-xs px-2 py-1 rounded bg-slate-700 hover:bg-slate-600"
        onClick={() => navigator.clipboard?.writeText(command)}
      >
        copy
      </button>
    </div>
  );
}

export default function ChaosPanel({ nodeIds }) {
  if (nodeIds.length === 0) {
    return <div className="text-slate-400 text-sm">No nodes discovered yet.</div>;
  }

  return (
    <div className="space-y-4 text-sm">
      {nodeIds.map((id) => (
        <div key={id}>
          <p className="font-semibold mb-1">{id}</p>
          <CommandRow label="kill" command={`docker compose stop ${id}`} />
          <CommandRow label="restart" command={`docker compose start ${id}`} />
        </div>
      ))}
      <div>
        <p className="font-semibold mb-1">partition (isolate a node's network)</p>
        <p className="text-xs text-slate-500 mb-1">
          Add a temporary docker-compose.override.yml giving the target its own network, then re-apply:
        </p>
        <CommandRow label="apply" command="docker compose up -d" />
        <p className="text-xs text-slate-500 mt-1">
          See scripts/adversarial_scenarios.py's category 3 for a worked override example.
        </p>
      </div>
    </div>
  );
}
