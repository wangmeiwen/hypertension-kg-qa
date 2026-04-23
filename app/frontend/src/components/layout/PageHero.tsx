import type { ReactNode } from "react";

interface PageHeroProps {
  eyebrow?: string;
  title: string;
  description?: string;
  actions?: ReactNode;
}

export default function PageHero({
  eyebrow,
  title,
  description,
  actions = null,
}: PageHeroProps) {
  return (
    <section className="page-hero">
      <div className="page-hero-copy">
        {eyebrow ? <div className="page-hero-eyebrow">{eyebrow}</div> : null}
        <h1>{title}</h1>
        {description ? <p>{description}</p> : null}
      </div>
      {actions ? <div className="page-hero-actions">{actions}</div> : null}
    </section>
  );
}
