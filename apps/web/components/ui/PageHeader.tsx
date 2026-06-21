import type { ReactNode } from "react";

interface PageHeaderProps {
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  backHref?: string;
  backLabel?: string;
}

/** Consistent page top section. Optional back link, subtitle, right actions. */
export function PageHeader({
  title,
  subtitle,
  actions,
  backHref,
  backLabel = "← Back",
}: PageHeaderProps) {
  return (
    <div>
      {backHref ? (
        <a href={backHref} className="back-link">
          {backLabel}
        </a>
      ) : null}
      <div className="page-header">
        <div>
          <h1 className="page-header__title">{title}</h1>
          {subtitle ? <div className="page-header__subtitle">{subtitle}</div> : null}
        </div>
        {actions ? <div className="page-header__actions">{actions}</div> : null}
      </div>
    </div>
  );
}
