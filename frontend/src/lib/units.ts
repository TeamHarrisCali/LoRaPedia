import { useSettings } from "./settings";
import type { Settings } from "./types";

/** The data is always kept in Celsius and kilometres; only what is shown changes. */
export const KM_PER_MILE = 1.609344;

export function fmtTemp(c: number | null | undefined, unit: Settings["temperature_unit"], digits = 1): string {
  if (c === null || c === undefined) return "–";
  return unit === "F" ? `${(c * 9 / 5 + 32).toFixed(digits)} °F` : `${c.toFixed(digits)} °C`;
}

export function fmtDistance(km: number | null | undefined, unit: Settings["distance_unit"]): string {
  if (km === null || km === undefined) return "–";
  if (unit === "mi") { const mi = km / KM_PER_MILE; return mi < 0.1 ? `${Math.round(mi * 5280)} ft` : `${mi < 10 ? mi.toFixed(1) : Math.round(mi)} mi`; }
  return km < 1 ? `${Math.round(km * 1000)} m` : `${km < 10 ? km.toFixed(1) : Math.round(km)} km`;
}

export function distanceKm(aLat: number, aLon: number, bLat: number, bLon: number): number {
  const rad = (d: number) => (d * Math.PI) / 180;
  const h = Math.sin(rad(bLat - aLat) / 2) ** 2 + Math.cos(rad(aLat)) * Math.cos(rad(bLat)) * Math.sin(rad(bLon - aLon) / 2) ** 2;
  return 2 * 6371 * Math.asin(Math.min(1, Math.sqrt(h)));
}

export function useUnits(): { temperature: Settings["temperature_unit"]; distance: Settings["distance_unit"] } {
  const { settings } = useSettings();
  return { temperature: settings?.temperature_unit ?? "C", distance: settings?.distance_unit ?? "km" };
}
