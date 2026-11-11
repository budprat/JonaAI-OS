import "@testing-library/jest-dom";

// Polyfill APIs not implemented in jsdom

// Node 26's own experimental localStorage shadows jsdom's and is undefined without a
// storage file, so tests that use it fail there unless it's supplied.
if (typeof window !== "undefined" && !window.localStorage) {
  const store = new Map<string, string>();
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: {
      get length() { return store.size; },
      key: (i: number) => [...store.keys()][i] ?? null,
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => void store.set(k, String(v)),
      removeItem: (k: string) => void store.delete(k),
      clear: () => store.clear(),
    } satisfies Storage,
  });
}

// ResizeObserver — required by Radix UI, Leaflet, Shadcn, @tanstack/react-virtual.
// Fires the callback on observe() so virtualizers detect container size immediately.
class ResizeObserverPolyfill {
  private callback: ResizeObserverCallback;
  private targets: Element[] = [];

  constructor(callback: ResizeObserverCallback) {
    this.callback = callback;
  }

  observe(target: Element) {
    this.targets.push(target);
    // Fire callback asynchronously so the virtualizer gets an initial measurement
    Promise.resolve().then(() => {
      const entry = {
        target,
        contentRect: target.getBoundingClientRect(),
        borderBoxSize: [{ blockSize: 600, inlineSize: 300 }],
        contentBoxSize: [{ blockSize: 600, inlineSize: 300 }],
        devicePixelContentBoxSize: [{ blockSize: 600, inlineSize: 300 }],
      } as unknown as ResizeObserverEntry;
      this.callback([entry], this as unknown as ResizeObserver);
    });
  }

  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver ??=
  ResizeObserverPolyfill as unknown as typeof ResizeObserver;

// IntersectionObserver — required by lazy-loading and visibility-based components
class IntersectionObserverPolyfill {
  readonly root = null;
  readonly rootMargin = "";
  readonly thresholds: readonly number[] = [];
  observe() {}
  unobserve() {}
  disconnect() {}
  takeRecords(): IntersectionObserverEntry[] {
    return [];
  }
}
globalThis.IntersectionObserver ??=
  IntersectionObserverPolyfill as unknown as typeof IntersectionObserver;

// matchMedia — required by useIsMobile() and theme detection
Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
});

// URL.createObjectURL / revokeObjectURL — required by map tile / blob handling
URL.createObjectURL ??= () => "blob:test-placeholder";
URL.revokeObjectURL ??= () => {};

// Element dimensions — jsdom has no layout engine, so clientHeight/scrollHeight
// are always 0. @tanstack/react-virtual needs non-zero viewport dimensions to
// decide which items are visible. Default to a reasonable viewport size.
Object.defineProperty(HTMLElement.prototype, "clientHeight", {
  configurable: true,
  get() {
    // Return the explicit style height if set, otherwise a reasonable default
    return parseInt(this.style?.height, 10) || 600;
  },
});

Object.defineProperty(HTMLElement.prototype, "scrollHeight", {
  configurable: true,
  get() {
    return parseInt(this.style?.height, 10) || 600;
  },
});

// getBoundingClientRect — needed by virtualizer measurement
const origGetBCR = Element.prototype.getBoundingClientRect;
Element.prototype.getBoundingClientRect = function () {
  const rect = origGetBCR.call(this);
  // If jsdom returned all zeros, provide reasonable defaults
  if (rect.height === 0 && rect.width === 0) {
    return {
      ...rect,
      height: 100,
      width: 300,
      top: 0,
      left: 0,
      bottom: 100,
      right: 300,
      x: 0,
      y: 0,
    };
  }
  return rect;
};
