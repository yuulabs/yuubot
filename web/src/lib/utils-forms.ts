/** Bind a controlled input onChange event to a state setter for a specific key. */
export function bind<T extends Record<string, unknown>>(
  setter: (value: T) => void,
  current: T,
  key: keyof T,
) {
  return (event: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) => {
    setter({ ...current, [key]: event.target.value });
  };
}

/** Append a value to a CSV string stored under a given key.
 *
 * If the value is empty or already present, the setter is not called.
 */
export function appendCsv<T extends Record<string, string>>(
  setter: (value: T) => void,
  current: T,
  key: keyof T,
  value: string,
) {
  if (!value) return;
  const existing = String(current[key] || "").split(",").filter(Boolean);
  if (!existing.includes(value)) {
    setter({ ...current, [key]: [...existing, value].join(",") });
  }
}
