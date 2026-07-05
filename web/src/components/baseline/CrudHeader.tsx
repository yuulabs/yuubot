// CrudHeader — section title + count pill (demo .crud-head).
interface CrudHeaderProps {
  title: string;
  count?: number;
}

export function CrudHeader({ title, count }: CrudHeaderProps) {
  return (
    <div className="crud-head">
      <h2 className="crud-head__title">{title}</h2>
      {count != null && <span className="crud-head__count">{count}</span>}
    </div>
  );
}
