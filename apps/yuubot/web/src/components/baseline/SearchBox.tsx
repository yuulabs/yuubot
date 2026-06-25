// SearchBox — demo .search: magnifier icon + input + ⌘K kbd hint.
interface SearchBoxProps {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
}

export function SearchBox({ value, onChange, placeholder = "搜索…" }: SearchBoxProps) {
  return (
    <div className="search">
      <svg className="search__icon" viewBox="0 0 24 24">
        <circle cx="11" cy="11" r="7" />
        <path d="M20 20l-3.5-3.5" />
      </svg>
      <input
        type="text"
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
      />
      <kbd>⌘K</kbd>
    </div>
  );
}
