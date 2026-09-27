export function StatusView({ value }: { value: unknown }) {
  if (value === null || value === undefined) return <span>—</span>;
  if (typeof value !== "object") return <span>{String(value)}</span>;
  if (Array.isArray(value)) return <ul className="status-tree">{value.map((item, index) => <li key={index}><StatusView value={item} /></li>)}</ul>;
  const entries = Object.entries(value as Record<string, unknown>);
  if (!entries.length) return <span>—</span>;
  return <dl className="status-tree">{entries.map(([key, item]) => <div key={key}>
    <dt>{key}</dt><dd><StatusView value={item} /></dd>
  </div>)}</dl>;
}
