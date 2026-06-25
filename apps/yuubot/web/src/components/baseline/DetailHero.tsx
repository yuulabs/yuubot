// DetailHero — avatar glyph + title + sub + meta row (demo .detail-hero).
import type { ReactNode } from "react";

interface DetailHeroProps {
  avatar: string;
  title: string;
  sub?: string;
  meta?: ReactNode;
}

export function DetailHero({ avatar, title, sub, meta }: DetailHeroProps) {
  return (
    <div className="detail-hero">
      <div className="detail-hero__avatar">{avatar}</div>
      <div>
        <h1 className="page-title" style={{ marginBottom: 0 }}>{title}</h1>
        {sub && <p className="page-sub">{sub}</p>}
        {meta && <div className="detail-hero__meta">{meta}</div>}
      </div>
    </div>
  );
}
