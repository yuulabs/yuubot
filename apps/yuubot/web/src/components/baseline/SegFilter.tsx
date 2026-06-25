// SegFilter — demo .seg multi-segment count filter.
interface SegOption<T extends string> {
  value: T;
  label: string;
  count?: number;
}

interface SegFilterProps<T extends string> {
  options: SegOption<T>[];
  value: T;
  onChange: (value: T) => void;
}

export function SegFilter<T extends string>({ options, value, onChange }: SegFilterProps<T>) {
  return (
    <div className="seg" role="tablist">
      {options.map((opt) => (
        <button
          key={opt.value}
          type="button"
          role="tab"
          aria-selected={opt.value === value}
          className={`seg__btn${opt.value === value ? " is-active" : ""}`}
          onClick={() => onChange(opt.value)}
        >
          {opt.label}
          {opt.count != null && <span className="seg__cnt">{opt.count}</span>}
        </button>
      ))}
    </div>
  );
}
