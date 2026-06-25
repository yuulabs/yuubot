// IngressFlow — demo .ingress-flow 4-node pipeline diagram.
interface IngressFlowProps {
  nodes?: string[];
}

export function IngressFlow({
  nodes = ["Integration", "Ingress Rule", "Actor Mailbox", "Actor Runtime"],
}: IngressFlowProps) {
  return (
    <div className="ingress-flow">
      {nodes.map((node, i) => (
        <FlowNode key={i} label={node} accent={i === 1} last={i === nodes.length - 1} />
      ))}
    </div>
  );
}

function FlowNode({ label, accent, last }: { label: string; accent: boolean; last: boolean }) {
  return (
    <>
      <span className={`ingress-flow__node${accent ? " ingress-flow__node--accent" : ""}`}>{label}</span>
      {!last && (
        <svg className="ingress-flow__arrow" viewBox="0 0 24 24" aria-hidden="true">
          <path d="M5 12h14M13 6l6 6-6 6" />
        </svg>
      )}
    </>
  );
}
