import type { ExpoConfig } from "expo/config";

// API base URL: EXPO_PUBLIC_API_URL (e.g. https://quantera.example.com or, on the Android
// emulator against a local server, http://10.0.2.2:8002). Cleartext HTTP is allowed only in
// development builds (APP_ENV=development); release builds require HTTPS.
const dev = process.env.APP_ENV === "development";

const config: ExpoConfig = {
  name: "Quantera",
  slug: "quantera",
  scheme: "quantera",
  version: "0.1.0",
  orientation: "portrait",
  icon: "./assets/icon.png",
  userInterfaceStyle: "automatic",
  android: {
    package: "com.quantera.app",
    adaptiveIcon: {
      backgroundColor: "#E6F4FE",
      foregroundImage: "./assets/android-icon-foreground.png",
      backgroundImage: "./assets/android-icon-background.png",
      monochromeImage: "./assets/android-icon-monochrome.png",
    },
    // Nothing is backed up to the cloud: tokens live in the keystore, caches hold no personal data.
    allowBackup: false,
    permissions: ["android.permission.POST_NOTIFICATIONS"],
    blockedPermissions: ["android.permission.RECORD_AUDIO", "android.permission.ACCESS_FINE_LOCATION"],
    predictiveBackGestureEnabled: false,
  },
  plugins: [
    "expo-router",
    "expo-secure-store",
    ["expo-notifications", { color: "#2457d6" }],
    ...(dev ? [["expo-build-properties", { android: { usesCleartextTraffic: true } }] as [string, object]] : []),
  ],
  extra: { apiUrl: process.env.EXPO_PUBLIC_API_URL ?? "http://10.0.2.2:8002" },
  experiments: { typedRoutes: false },
};

export default config;
