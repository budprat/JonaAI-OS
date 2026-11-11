import { Component, type ReactNode, type ErrorInfo } from "react";
import { AlertTriangle, RefreshCw } from "lucide-react";

interface ErrorBoundaryProps {
  children: ReactNode;
  /** Label shown in the error UI to identify the failing section */
  section?: string;
  /** Optional compact mode for sidebar/panel boundaries */
  compact?: boolean;
  /** Optional fallback to render instead of the default error UI */
  fallback?: ReactNode;
}

interface ErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
}

/**
 * React Error Boundary that catches rendering errors in child components
 * and displays a recovery UI instead of crashing the entire app.
 *
 * Usage:
 *   <ErrorBoundary section="Map">
 *     <MapViewer ... />
 *   </ErrorBoundary>
 */
export class ErrorBoundary extends Component<
  ErrorBoundaryProps,
  ErrorBoundaryState
> {
  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Log to console for debugging; future: send to error tracking service
    console.error(
      `[ErrorBoundary${this.props.section ? `: ${this.props.section}` : ""}]`,
      error,
      info.componentStack,
    );
  }

  private handleRetry = () => {
    this.setState({ hasError: false, error: null });
  };

  render() {
    if (!this.state.hasError) {
      return this.props.children;
    }

    if (this.props.fallback) {
      return this.props.fallback;
    }

    const { section, compact } = this.props;
    const { error } = this.state;

    if (compact) {
      return (
        <div className="flex flex-col items-center justify-center gap-2 p-4 text-center text-sm text-muted-foreground">
          <AlertTriangle className="h-5 w-5 text-destructive" />
          <p>
            {section
              ? `${section} encountered an error`
              : "Something went wrong"}
          </p>
          <button
            onClick={this.handleRetry}
            className="flex items-center gap-1 rounded-md bg-primary px-3 py-1 text-xs text-primary-foreground hover:bg-primary/90"
          >
            <RefreshCw className="h-3 w-3" />
            Retry
          </button>
        </div>
      );
    }

    return (
      <div className="flex h-full flex-col items-center justify-center gap-4 p-8 text-center">
        <div className="rounded-full bg-destructive/10 p-3">
          <AlertTriangle className="h-8 w-8 text-destructive" />
        </div>
        <div className="max-w-md space-y-2">
          <h2 className="text-lg font-semibold text-foreground">
            {section ? `${section} Error` : "Something went wrong"}
          </h2>
          <p className="text-sm text-muted-foreground">
            An unexpected error occurred
            {section ? ` in the ${section.toLowerCase()} panel` : ""}. Try
            retrying, or reload the page if the issue persists.
          </p>
          {error && (
            <details className="mt-2 text-left">
              <summary className="cursor-pointer text-xs text-muted-foreground hover:text-foreground">
                Error details
              </summary>
              <pre className="mt-1 max-h-32 overflow-auto rounded-md bg-muted p-2 text-xs font-mono text-destructive">
                {error.message}
              </pre>
            </details>
          )}
        </div>
        <div className="flex gap-2">
          <button
            onClick={this.handleRetry}
            className="flex items-center gap-1.5 rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground hover:bg-primary/90"
          >
            <RefreshCw className="h-4 w-4" />
            Retry
          </button>
          <button
            onClick={() => window.location.reload()}
            className="rounded-md border border-border px-4 py-2 text-sm text-foreground hover:bg-muted"
          >
            Reload Page
          </button>
        </div>
      </div>
    );
  }
}
