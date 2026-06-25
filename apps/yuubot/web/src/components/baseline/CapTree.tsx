// CapTree — capability grouping tree (demo .cap-tree + .cap-group).
// Groups by source; the colored cb span toggles the whole group; the caret
// expands/collapses the group. Individual caps toggle via their checkboxes.
import { useState, type MouseEvent } from "react";

interface CapItem {
  capabilityId: string;
  name: string;
  description: string;
}

interface CapGroup {
  sourceId: string;
  sourceName: string;
  capabilities: CapItem[];
}

interface CapTreeProps {
  groups: CapGroup[];
  selectedIds: string[];
  onChange: (selectedIds: string[]) => void;
}

export function CapTree({ groups, selectedIds, onChange }: CapTreeProps) {
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const selected = new Set(selectedIds);

  const toggle = (id: string) => {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    onChange([...next]);
  };

  const toggleGroup = (g: CapGroup) => {
    const ids = g.capabilities.map((c) => c.capabilityId);
    const allOn = ids.every((id) => selected.has(id));
    const next = new Set(selected);
    if (allOn) ids.forEach((id) => next.delete(id));
    else ids.forEach((id) => next.add(id));
    onChange([...next]);
  };

  return (
    <div className="cap-tree" role="tree">
      {groups.map((g) => {
        const ids = g.capabilities.map((c) => c.capabilityId);
        const on = ids.filter((id) => selected.has(id)).length;
        const state = on === 0 ? "off" : on === ids.length ? "on" : "ind";
        const isOpen = open[g.sourceId] ?? true;
        return (
          <div key={g.sourceId} className={`cap-group${isOpen ? " is-open" : ""}`}>
            <div className="cap-group__head">
              <button
                type="button"
                aria-label={isOpen ? "收起" : "展开"}
                onClick={() => setOpen((s) => ({ ...s, [g.sourceId]: !isOpen }))}
              >
                <svg className="cap-group__caret" viewBox="0 0 24 24"><path d="M9 6l6 6-6 6" /></svg>
              </button>
              <span
                role="checkbox"
                aria-checked={state === "on" ? true : state === "ind" ? "mixed" : false}
                tabIndex={0}
                className={`cap-group__cb${state === "on" ? " is-checked" : state === "ind" ? " is-indeterminate" : ""}`}
                onClick={(e: MouseEvent) => { e.stopPropagation(); toggleGroup(g); }}
                onKeyDown={(e) => { if (e.key === " " || e.key === "Enter") { e.preventDefault(); toggleGroup(g); } }}
              />
              <span className="cap-group__name">{g.sourceName}</span>
              <span className="cap-group__count">{on}/{ids.length}</span>
            </div>
            {isOpen && (
              <div className="cap-group__list">
                {g.capabilities.map((c) => (
                  <label key={c.capabilityId} className="cap-item">
                    <input
                      type="checkbox"
                      checked={selected.has(c.capabilityId)}
                      onChange={() => toggle(c.capabilityId)}
                    />
                    <span className="cb" aria-hidden="true" />
                    <span className="cap-item__body">
                      <span className="cap-item__name">{c.name}</span>
                      <span className="cap-item__via">{c.description}</span>
                      <span className="cap-item__src">{c.capabilityId}</span>
                    </span>
                  </label>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
