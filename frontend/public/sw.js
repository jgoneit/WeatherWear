/*
 * WeatherWear Service_Worker (Req 16.1).
 *
 * push: show the payload title/body as one system notification. Every brief uses the
 *       same tag, so a newer brief replaces the older one instead of stacking up.
 * notificationclick: bring an open WeatherWear window to the front and move it to the
 *       payload url, or open the url in a new window when none is open.
 *
 * The page registers this file only after the user taps "알림 켜기" (Req 16.2).
 * No secrets or API addresses live here: the payload carries everything it shows.
 */
const NOTIFICATION_TAG = "weatherwear-daily-brief";
const DEFAULT_TITLE = "WeatherWear";

self.addEventListener("install", () => {
  // A new worker replaces the old one without waiting for every tab to close.
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  // Control already-open pages so notificationclick can navigate them.
  event.waitUntil(self.clients.claim());
});

function readPayload(event) {
  if (!event.data) return {};
  try {
    const data = event.data.json();
    return data && typeof data === "object" ? data : {};
  } catch {
    return {};
  }
}

/** Same-origin path or URL from the payload; anything else falls back to the app root. */
function targetUrl(raw) {
  const origin = self.location.origin;
  try {
    const url = new URL(typeof raw === "string" && raw ? raw : "/", origin);
    return url.origin === origin ? url.href : new URL("/", origin).href;
  } catch {
    return new URL("/", origin).href;
  }
}

self.addEventListener("push", (event) => {
  const data = readPayload(event);
  const title = typeof data.title === "string" && data.title ? data.title : DEFAULT_TITLE;
  const body = typeof data.body === "string" ? data.body : "";
  event.waitUntil(
    self.registration.showNotification(title, {
      body,
      tag: NOTIFICATION_TAG,
      data: { url: targetUrl(data.url) },
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = targetUrl(event.notification.data && event.notification.data.url);
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
      const open = list.find((client) => {
        try {
          return new URL(client.url).origin === self.location.origin;
        } catch {
          return false;
        }
      });
      if (!open) return self.clients.openWindow(url);
      return open
        .focus()
        .then((focused) => {
          const client = focused || open;
          // navigate() rejects for a window this worker does not control yet.
          return typeof client.navigate === "function" ? client.navigate(url) : client;
        })
        .catch(() => self.clients.openWindow(url));
    }),
  );
});
