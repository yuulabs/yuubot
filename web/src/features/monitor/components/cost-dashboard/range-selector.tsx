import type { UsageRange } from "@/hooks/use-usage-analytics";
import { RANGE_LABELS, USAGE_RANGES } from "./constants";

export function UsageRangeSelector({
  range,
  onChange,
}: {
  range: UsageRange;
  onChange: (range: UsageRange) => void;
}) {
  return (
    <div className="seg monitor-range" role="tablist" aria-label="Usage range">
      {USAGE_RANGES.map((item) => (
        <button
          key={item}
          type="button"
          role="tab"
          aria-selected={range === item}
          className={`seg__btn ${range === item ? "is-active" : ""}`}
          onClick={() => onChange(item)}
        >
          {RANGE_LABELS[item]}
        </button>
      ))}
    </div>
  );
}
