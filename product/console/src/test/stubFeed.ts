import type { Analysis, FeedLike, FeedTransport } from '../lib/api';

/**
 * A `Feed` that emits a fixed list and never touches the network.
 *
 * Views read the feed from `ShellProvider` now (one socket for the whole
 * console instead of one per view), so a test that renders a view in
 * isolation has to supply both the provider and the feed. Passing this to
 * `<ShellProvider feed={…}>` — and, where the test drives updates, to the
 * view's own `feed` prop — keeps every test offline.
 */
export function makeStubFeed(items: Analysis[] = [], transport: FeedTransport = 'WS') {
  const listeners: ((items: Analysis[]) => void)[] = [];

  const stub = {
    online: transport === 'WS',
    transport,
    lastItems: items,

    subscribe(cb: (items: Analysis[]) => void) {
      listeners.push(cb);
      cb(stub.lastItems);
      return () => {
        const i = listeners.indexOf(cb);
        if (i >= 0) listeners.splice(i, 1);
      };
    },

    subscribeTransport(cb: (t: FeedTransport) => void) {
      cb(transport);
      return () => {};
    },

    close() {},

    /** Push a new list to every subscriber, as a WS frame would. */
    emit(next: Analysis[]) {
      stub.lastItems = next;
      for (const l of [...listeners]) l(next);
    },
  };

  return stub satisfies FeedLike;
}
