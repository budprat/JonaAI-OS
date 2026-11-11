import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatDate(date: string | Date): string {
  return new Date(date).toLocaleDateString("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

export function formatDateTime(date: string | Date): string {
  return new Date(date).toLocaleString("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 Bytes";
  const k = 1024;
  const sizes = ["Bytes", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + " " + sizes[i];
}

export function getQualityGrade(score: number): { grade: string; color: string } {
  if (score >= 90) return { grade: "A", color: "quality-a" };
  if (score >= 75) return { grade: "B", color: "quality-b" };
  if (score >= 60) return { grade: "C", color: "quality-c" };
  if (score >= 40) return { grade: "D", color: "quality-d" };
  return { grade: "F", color: "quality-f" };
}

export function generateId(): string {
  return crypto.randomUUID();
}

/**
 * Append the auth token to a URL as a query parameter.
 * Needed for endpoints loaded via <img src="..."> or Leaflet,
 * where the browser cannot send Authorization headers.
 */
export function authUrl(url: string, accessToken?: string | null): string {
  // A saved visualization's URL can come from someone else, so the token stays on this origin.
  try {
    if (new URL(url, window.location.origin).origin !== window.location.origin) return url;
  } catch {
    return url;
  }
  let token = accessToken;
  if (!token) {
    const raw = window.localStorage.getItem("jonaai-token");
    if (!raw) return url;
    try {
      token = JSON.parse(raw);
    } catch {
      return url;
    }
  }
  if (!token) return url;
  try {
    const sep = url.includes("?") ? "&" : "?";
    return `${url}${sep}token=${encodeURIComponent(token)}`;
  } catch {
    return url;
  }
}

/**
 * Download a file from a route that needs the Authorization header. A plain
 * <a href> can't send one, so fetch it, then save the blob.
 */
export async function downloadAuthenticatedFile(
  url: string,
  filename: string,
  authToken: string | null,
): Promise<void> {
  const response = await fetch(url, {
    headers: authToken ? { Authorization: `Bearer ${authToken}` } : {},
  });

  if (!response.ok) {
    throw new Error(`Download failed with status ${response.status}`);
  }

  const blob = await response.blob();
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(objectUrl);
}
