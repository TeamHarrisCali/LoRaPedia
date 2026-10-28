import type { SVGProps } from "react";

const PATHS: Record<string, string> = {
  dashboard: "M3 13h8V3H3zM13 21h8V11h-8zM3 21h8v-6H3zM13 3v6h8V3z",
  feed: "M4 6h16M4 12h16M4 18h10",
  book: "M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2zM4 21V5M9 7h6",
  cpu: "M9 3v2M15 3v2M9 19v2M15 19v2M3 9h2M3 15h2M19 9h2M19 15h2M7 5h10a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2zM9 9h6v6H9z",
  radio: "M12 12v9M8.5 9a5 5 0 0 1 7 0M5.5 6a9 9 0 0 1 13 0M10 12h4",
  settings: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z",
  wand: "M15 4V2M15 16v-2M8 9H6M24 9h-2M19.5 4.5l1-1M10.5 13.5l-8 8 1.5 1.5 8-8M19.5 13.5l1 1M10.5 4.5l1-1M13 7l4 4",
  check: "M5 13l4 4L19 7",
  x: "M6 6l12 12M18 6L6 18",
  play: "M7 4l13 8-13 8z",
  pause: "M8 5v14M16 5v14",
  stop: "M6 6h12v12H6z",
  refresh: "M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7",
  download: "M12 3v12m0 0l-4-4m4 4l4-4M4 19h16",
  trash: "M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3",
  chevron: "M9 6l6 6-6 6",
  search: "M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM21 21l-4.3-4.3",
  send: "M22 2L11 13M22 2l-7 20-4-9-9-4z",
  alert: "M12 9v4m0 4h.01M10.3 3.9L1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z",
  upload: "M12 15V3m0 0L8 7m4-4l4 4M4 19h16",
  nodes: "M4 6a2 2 0 1 0 4 0 2 2 0 1 0-4 0zM16 8a2 2 0 1 0 4 0 2 2 0 1 0-4 0zM10 18a2 2 0 1 0 4 0 2 2 0 1 0-4 0zM8 7l8 1M7.5 8l3.5 9M17.5 10l-4 7",
};

export function Icon({ name, size = 18, ...rest }: { name: keyof typeof PATHS | string; size?: number } & SVGProps<SVGSVGElement>) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}
         strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...rest}>
      <path d={PATHS[name] ?? ""} />
    </svg>
  );
}
