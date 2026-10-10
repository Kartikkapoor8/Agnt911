import { useState, type ReactNode } from "react";

interface Props {
  className: string; // positions the panel; all placement lives in styles.css
  title?: ReactNode;
  actions?: ReactNode; // extra header controls, still visible when collapsed
  collapsible?: boolean;
  defaultCollapsed?: boolean;
  children?: ReactNode;
}

/** The one floating panel over the map: translucent dark glass, thin border, optional collapse. */
export function Panel({ className, title, actions, collapsible = false, defaultCollapsed = false, children }: Props) {
  const [collapsed, setCollapsed] = useState(defaultCollapsed);
  const open = !collapsible || !collapsed;
  return (
    <section className={`float-panel ${className}${open ? "" : " collapsed"}`}>
      {(title || actions || collapsible) && (
        <header className="float-panel-head">
          {collapsible ? (
            <button className="float-panel-toggle" aria-expanded={open} onClick={() => setCollapsed(!collapsed)}>
              <span className="chevron" aria-hidden />
              {title}
            </button>
          ) : (
            title && <h2>{title}</h2>
          )}
          {actions && <div className="float-panel-actions">{actions}</div>}
        </header>
      )}
      {open && children}
    </section>
  );
}
