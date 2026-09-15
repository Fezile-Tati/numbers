import { Component, type ErrorInfo, type ReactNode } from "react";

/**
 * Catches render errors thrown by the routed subtree.
 *
 * Without this, a single throw anywhere under <Routes> unmounts the whole
 * routed tree and the user gets a blank white page with nothing but a console
 * trace — which is exactly how a dashboard plugin calling an SDK component the
 * wrong way used to present. A plugin is third-party code by definition, so it
 * has to be allowed to fail loudly but locally: the sidebar, header and theme
 * stay on screen and the page itself explains what happened.
 *
 * Resets on navigation (the key passed by the caller), so moving to another
 * page recovers without a reload.
 */
interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

export class RouteErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("[route] render failed:", error, info.componentStack);
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;

    return (
      <div className="flex flex-col gap-3 rounded-lg border border-border bg-card p-6">
        <h2 className="text-lg font-semibold text-midground">
          This page failed to render
        </h2>
        <p className="text-sm text-text-secondary">
          The rest of the dashboard is unaffected — pick another page in the
          sidebar, or reload to try again. If this page comes from a plugin,
          the plugin is the likely cause.
        </p>
        <pre className="overflow-auto rounded bg-background p-3 text-xs text-text-tertiary">
          {error.message}
        </pre>
      </div>
    );
  }
}
