/**
 * Trigger subtle native-like haptic feedback on supported mobile browsers.
 */
export function triggerHaptic(durationMs = 8): void {
  if (typeof navigator !== "undefined" && typeof navigator.vibrate === "function") {
    try {
      navigator.vibrate(durationMs);
    } catch {
      // Ignore vibration errors on restricted platforms
    }
  }
}

