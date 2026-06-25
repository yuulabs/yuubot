// LayoutToggle — demo .layout-toggle: grid/list switcher.
type LayoutValue = "grid" | "list";

interface LayoutToggleProps {
  value: LayoutValue;
  onChange: (value: LayoutValue) => void;
}

export function LayoutToggle({ value, onChange }: LayoutToggleProps) {
  return (
    <div className="layout-toggle" role="group" aria-label="视图布局">
      <button
        type="button"
        className={`lt-btn${value === "grid" ? " is-active" : ""}`}
        aria-pressed={value === "grid"}
        title="网格视图"
        onClick={() => onChange("grid")}
      >
        <svg viewBox="0 0 24 24"><path d="M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z" /></svg>
      </button>
      <button
        type="button"
        className={`lt-btn${value === "list" ? " is-active" : ""}`}
        aria-pressed={value === "list"}
        title="列表视图"
        onClick={() => onChange("list")}
      >
        <svg viewBox="0 0 24 24"><path d="M3 5h18M3 12h18M3 19h18" /></svg>
      </button>
    </div>
  );
}
