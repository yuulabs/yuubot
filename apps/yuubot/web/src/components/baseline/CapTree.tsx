// CapTree — flat integration-instance selector for Capability Sets.
// CapabilitySet.integration_ids stores IntegrationRecord.id values; capability
// methods are shown only as a count/summary and are not selectable.
import type { MouseEvent } from "react";

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
  const selected = new Set(selectedIds);

  const toggle = (id: string) => {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    onChange([...next]);
  };

  return (
    <div className="cap-tree" role="list">
      {groups.map((g) => {
        const checked = selected.has(g.sourceId);
        const summary = g.capabilities
          .map((capability) => capability.name)
          .filter(Boolean)
          .join(", ");
        return (
          <div key={g.sourceId} className="cap-group" role="listitem">
            <div className="cap-group__head">
              <span
                role="checkbox"
                aria-checked={checked}
                tabIndex={0}
                className={`cap-group__cb${checked ? " is-checked" : ""}`}
                onClick={(e: MouseEvent) => {
                  e.stopPropagation();
                  toggle(g.sourceId);
                }}
                onKeyDown={(e) => {
                  if (e.key === " " || e.key === "Enter") {
                    e.preventDefault();
                    toggle(g.sourceId);
                  }
                }}
              />
              <span className="cap-group__name">{g.sourceName}</span>
              <span className="cap-group__count">{g.capabilities.length}</span>
            </div>
            {summary && (
              <div className="cap-group__list">
                <div className="cap-item">
                  <span className="cap-item__body">
                    <span className="cap-item__via">{summary}</span>
                    <span className="cap-item__src">{g.sourceId}</span>
                  </span>
                </div>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
