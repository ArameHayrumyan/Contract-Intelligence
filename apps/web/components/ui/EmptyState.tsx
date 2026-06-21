import type { ReactNode } from "react";

interface EmptyStateProps {
  icon?: ReactNode;
  title: string;
  description?: string;
  action?: ReactNode;
}

/** Consistent zero-state for every empty list — no more plain grey text. */
export function EmptyState({ icon, title, description, action }: EmptyStateProps) {
  return (
    <div className="empty-state">
      {icon ?? <DefaultIcon />}
      <div className="empty-state__title">{title}</div>
      {description ? <p className="empty-state__desc">{description}</p> : null}
      {action}
    </div>
  );
}

function DefaultIcon() {
  return (
    <svg width="40" height="40" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d="M9 13h6m-3-3v6M5 3h9l5 5v13H5z"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinejoin="round"
      />
    </svg>
  );
}
