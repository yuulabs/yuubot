import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Empty } from "./baseline/Empty";

interface Column<T> {
  key: string;
  label: string;
  render: (row: T) => React.ReactNode;
  className?: string;
}

interface DataTableProps<T> {
  columns: Column<T>[];
  rows: T[];
  emptyLabel?: string;
  className?: string;
}

export function DataTable<T extends { id: string }>({
  columns,
  rows,
  emptyLabel = "No data",
  className,
}: DataTableProps<T>) {
  if (rows.length === 0) {
    return <Empty title={emptyLabel} />;
  }
  return (
    <div className={className ? `table-wrap ${className}` : "table-wrap"}>
      <Table className="data-table">
        <TableHeader>
          <TableRow>
            {columns.map((col) => (
              <TableHead key={col.key} className={col.className}>{col.label}</TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row) => (
            <TableRow key={row.id}>
              {columns.map((col) => (
                <TableCell key={col.key} className={col.className}>
                  {col.render(row)}
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
