import type { PushSubscriptionRecord } from "@/shared/types/api";
import { BASE, request } from "./client";

export function getVapidPublicKey(): Promise<{ public_key: string }> {
  return request<{ public_key: string }>(`${BASE}/notifications/vapid-public-key`);
}

export function savePushSubscription(body: { endpoint: string; keys: Record<string, string> }): Promise<PushSubscriptionRecord> {
  return request<PushSubscriptionRecord>(`${BASE}/notifications/subscriptions`, { method: "POST", body: JSON.stringify(body) });
}

export function deletePushSubscription(subscriptionId: string): Promise<{ id: string; deleted: boolean }> {
  return request<{ id: string; deleted: boolean }>(`${BASE}/notifications/subscriptions/${encodeURIComponent(subscriptionId)}`, {
    method: "DELETE",
  });
}

function urlBase64ToUint8Array(base64String: string): Uint8Array {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = window.atob(base64);
  const output = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i += 1) output[i] = raw.charCodeAt(i);
  return output;
}

export async function subscribePushNotifications(): Promise<PushSubscriptionRecord | null> {
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) return null;
  const permission = await Notification.requestPermission();
  if (permission !== "granted") return null;
  const registration = await navigator.serviceWorker.register("/sw.js");
  const { public_key: publicKey } = await getVapidPublicKey();
  const subscription = await registration.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: urlBase64ToUint8Array(publicKey),
  });
  const json = subscription.toJSON();
  if (!json.endpoint || !json.keys?.p256dh || !json.keys.auth) return null;
  return savePushSubscription({
    endpoint: json.endpoint,
    keys: { p256dh: json.keys.p256dh, auth: json.keys.auth },
  });
}

export async function requestBrowserNotificationPermission(): Promise<NotificationPermission> {
  if (!("Notification" in window)) return "denied";
  return Notification.requestPermission();
}
