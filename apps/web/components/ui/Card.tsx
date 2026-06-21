import type { ReactNode } from "react";

interface CardProps {
  title?: ReactNode;
  action?: ReactNode;
  footer?: ReactNode;
  className?: string;
  children: ReactNode;
}

/** Surface container with optional header (title + action) and footer slots. */
export function Card({ title, action, footer, className = "", children }: CardProps) {
  return (
    <div className={`card ${className}`.trim()}>
      {title || action ? (
        <div className="card__header">
          <div className="card__title">{title}</div>
          {action ? <div>{action}</div> : null}
        </div>
      ) : null}
      {children}
      {footer ? <div className="card__footer">{footer}</div> : null}
    </div>
  );
}
