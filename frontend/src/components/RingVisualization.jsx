const SIZE = 320;
const CENTER = SIZE / 2;
const RADIUS = SIZE / 2 - 20;

const COLORS = ["#38bdf8", "#f472b6", "#facc15", "#4ade80", "#a78bfa", "#fb923c"];

function colorFor(owner, owners) {
  const idx = owners.indexOf(owner);
  return COLORS[idx % COLORS.length];
}

export default function RingVisualization({ ring }) {
  if (!ring) {
    return <div className="text-slate-400 text-sm">No ring data yet.</div>;
  }

  const owners = Array.from(new Set(ring.points.map((p) => p.owner))).sort();

  return (
    <div>
      <svg width={SIZE} height={SIZE} className="mx-auto">
        <circle cx={CENTER} cy={CENTER} r={RADIUS} fill="none" stroke="#334155" strokeWidth="1" />
        {ring.points.map((p, i) => {
          const angle = p.position * 2 * Math.PI - Math.PI / 2;
          const x1 = CENTER + (RADIUS - 6) * Math.cos(angle);
          const y1 = CENTER + (RADIUS - 6) * Math.sin(angle);
          const x2 = CENTER + (RADIUS + 6) * Math.cos(angle);
          const y2 = CENTER + (RADIUS + 6) * Math.sin(angle);
          return (
            <line
              key={`${p.owner}-${i}`}
              x1={x1}
              y1={y1}
              x2={x2}
              y2={y2}
              stroke={colorFor(p.owner, owners)}
              strokeWidth="2"
            />
          );
        })}
      </svg>
      <div className="flex flex-wrap gap-3 justify-center mt-2 text-xs">
        {owners.map((owner) => (
          <span key={owner} className="flex items-center gap-1">
            <span className="inline-block w-2 h-2 rounded-full" style={{ background: colorFor(owner, owners) }} />
            {owner}
          </span>
        ))}
      </div>
      <p className="text-xs text-slate-500 text-center mt-1">
        {ring.points.length} virtual points ({ring.virtual_nodes} per node)
      </p>
    </div>
  );
}
