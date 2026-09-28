import { useEffect, type ReactNode } from "react";
import { createPortal } from "react-dom";

type Props = {
  title: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
};

/** One lightweight overlay used by every Lobby panel.
 *
 * Centered modal on desktop, near-fullscreen sheet on phones, closing on the
 * close button, the backdrop and Escape. Deliberately not a modal framework.
 */
export function OverlayPanel({ title, onClose, children, footer }: Props) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return createPortal(
    <div
      className="overlay"
      role="dialog"
      aria-modal="true"
      aria-label={title}
      onClick={(event) => { if (event.target === event.currentTarget) onClose(); }}
    >
      <section className="overlay-panel">
        <header className="overlay-head">
          <h2>{title}</h2>
          <button className="secondary compact-button overlay-close" onClick={onClose}>✕ 关闭</button>
        </header>
        <div className="overlay-body">{children}</div>
        {footer && <footer className="overlay-foot">{footer}</footer>}
      </section>
    </div>,
    document.body,
  );
}
