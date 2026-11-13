export function urlBase64ToUint8Array(base64String: string): Uint8Array {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const rawData = window.atob(base64);
  const outputArray = new Uint8Array(rawData.length);
  for (let i = 0; i < rawData.length; i += 1) {
    outputArray[i] = rawData.charCodeAt(i);
  }
  return outputArray;
}

export function isWebPushSupported(): boolean {
  return (
    typeof window !== "undefined" &&
    "serviceWorker" in navigator &&
    "PushManager" in window &&
    "Notification" in window
  );
}

export async function registerGemuseServiceWorker(): Promise<ServiceWorkerRegistration | null> {
  if (typeof window === "undefined" || !("serviceWorker" in navigator)) {
    return null;
  }
  return navigator.serviceWorker.register("/sw.js", { scope: "/" });
}

export async function subscribeWebPush(vapidPublicKey: string): Promise<boolean> {
  if (!isWebPushSupported() || !vapidPublicKey) {
    return false;
  }

  const permission = await Notification.requestPermission();
  if (permission !== "granted") {
    return false;
  }

  const registration = await registerGemuseServiceWorker();
  if (!registration) {
    return false;
  }

  const existing = await registration.pushManager.getSubscription();
  const appServerKey = urlBase64ToUint8Array(vapidPublicKey);
  const sub =
    existing ||
    (await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: appServerKey.buffer as ArrayBuffer,
    }));

  const subJson = sub.toJSON();
  const res = await fetch("/api/proxy/proactive/push/subscribe", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      endpoint: subJson.endpoint,
      keys: {
        p256dh: subJson.keys?.p256dh || "",
        auth: subJson.keys?.auth || "",
      },
      user_agent: navigator.userAgent.slice(0, 250),
    }),
  });

  return res.ok;
}

export async function getCurrentPushSubscriptionStatus(): Promise<boolean> {
  if (!isWebPushSupported()) {
    return false;
  }
  const reg = await navigator.serviceWorker.getRegistration("/");
  if (!reg) {
    return false;
  }
  const sub = await reg.pushManager.getSubscription();
  return Boolean(sub);
}

export async function unsubscribeWebPush(): Promise<boolean> {
  if (!isWebPushSupported()) {
    return false;
  }
  const reg = await navigator.serviceWorker.getRegistration("/");
  if (!reg) {
    return false;
  }
  const sub = await reg.pushManager.getSubscription();
  if (!sub) {
    return true;
  }
  return sub.unsubscribe();
}

