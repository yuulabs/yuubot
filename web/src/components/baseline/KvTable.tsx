// KvTable — key/value config overview table (demo .kv-table + .kv-row).
import type { ReactNode } from "react";

interface KvRow {
  key: string;
  value: ReactNode;
}

interface KvTableProps {
  rows: KvRow[];
}

export function KvTable({ rows }: KvTableProps) {
  return (
    <div className="kv-table">
      {rows.map((r) => (
        <div className="kv-row" key={r.key}>
          <span className="kv-row__k">{r.key}</span>
          <span className="kv-row__v">{r.value}</span>
        </div>
      ))}
    </div>
  );
}
