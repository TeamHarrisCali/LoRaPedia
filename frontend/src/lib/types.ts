export interface Settings {
  radio_method: "auto" | "simulator" | "serial" | "tcp" | "ble";
  radio_address: string;
  trigger_prefixes: string[];
  hop_limit: number;
  allowlist: string[];
  blocklist: string[];
  byte_limit: number;
  multipart_enabled: boolean;
  max_parts: number;
  auto_send_parts: boolean;
  send_delay_s: number;
  ai_fallback: boolean;
  auto_backup: boolean;
  temperature_unit: "C" | "F";
  distance_unit: "km" | "mi";
  mesh_record: boolean;
  mesh_retention_days: number;
  update_channel: "stable" | "beta";
  backup_keep: number;
  update_check: boolean;
  auto_update: boolean;
  wikipedia_update_check: boolean;
  wikipedia_auto_update: boolean;
  queue_when_limited: boolean;
  max_queued_per_node: number;
  ack_queued: boolean;
  rate_limit_queries: number;
  rate_limit_window_s: number;
  queue_max: number;
  llm_backend: "openai" | "mock";
  llm_base_url: string;
  llm_model: string;
  embed_backend: "ollama" | "hash";
  embed_base_url: string;
  embed_model: string;
  top_k: number;
  temperature: number;
  max_tokens: number;
  system_prompt: string;
}

export interface BackupItem { name: string; kind: "manual" | "auto" | "pre-update" | "before-restore" | "uploaded"; size: number; ts: number }
export interface BackupStatus { items: BackupItem[]; auto_backup: boolean; backup_keep: number; folder: string; total_size: number }

export interface MeshNode {
  id: string; num: number | null; name: string | null; short: string | null; hw: string | null; role: string | null;
  label: string | null; note: string | null; display: string; us: boolean; live: boolean; remembered: boolean;
  first_seen: number; last_seen: number; last_heard: number | null; age_s: number | null;
  hops: number | null; hops_measured: boolean; snr: number | null; rssi: number | null; via_mqtt: number | null; has_key: number | null;
  lat: number | null; lon: number | null; alt: number | null; pos_ts: number | null; pos_source: string | null;
  battery: number | null; voltage: number | null; channel_util: number | null; air_util_tx: number | null; uptime_s: number | null;
  temperature: number | null; humidity: number | null; pressure: number | null; metrics_ts: number | null;
}
export interface MeshSummary {
  nodes_total: number; heard_15m: number; heard_1h: number; heard_24h: number; direct: number; with_position: number;
  with_telemetry: number; via_mqtt: number; avg_channel_util: number | null; roles: Record<string, number>; hardware: Record<string, number>;
}
export interface MeshNodes { nodes: MeshNode[]; summary: MeshSummary; recording: boolean; retention_days: number; radio: string }
export interface TelemetryRow { ts: number; kind: string; battery: number | null; voltage: number | null; channel_util: number | null; air_util_tx: number | null; temperature: number | null; humidity: number | null; pressure: number | null }
export interface MeshNodeDetail { node: MeshNode; telemetry: TelemetryRow[]; positions: { ts: number; lat: number; lon: number; alt: number | null }[] }
export interface MeshData { recording: boolean; retention_days: number; size: number; datasets: Record<string, { rows: number; first: number | null; last: number | null }> }

export type MeshMetric = "battery" | "voltage" | "channel_util" | "air_util_tx" | "uptime_s" | "temperature" | "humidity" | "pressure";
export interface MeshSeries { metric: MeshMetric; hours: number; series: Record<string, [number, number][]> }
export interface MeshTrails { days: number; trails: Record<string, [number, number, number][]> }
export interface MeshSensors {
  hours: number; average: { temperature: number | null; humidity: number | null; pressure: number | null };
  nodes: { id: string; name: string; readings: number; last: number; temperature: number | null; humidity: number | null; pressure: number | null }[];
}
export interface MeshReading { ts: number; node_id: string; node_name: string | null; node_short: string | null; node_label: string | null; kind: string;
  battery: number | null; voltage: number | null; channel_util: number | null; air_util_tx: number | null; temperature: number | null; humidity: number | null; pressure: number | null; snr: number | null; hops: number | null }
export interface MeshPacketTrend { days: number; hours: [number, number][]; kinds: [string, number][] }
export interface MeshHopTrend { days: number; hours: [number, number, number][]; summary: { packets: number; average_relays: number | null; share_direct: number | null; furthest: number | null } }

export interface TraceHop { num: number | null; id: string | null; name: string | null; snr: number | null; lat: number | null; lon: number | null }
export interface TraceRow {
  id: number; ts: number; node_id: string; node_name?: string | null; hop_limit: number; status: "ok" | "timeout" | "error"; detail: string | null;
  latency_ms: number | null; relays_towards: number | null; relays_back: number | null; path_towards: TraceHop[] | null; path_back: TraceHop[] | null;
}
export interface TraceState { id: number; status: "waiting" | "ok" | "timeout" | "error"; node_id: string; detail?: string | null; trace?: TraceRow }
export interface MeshLink {
  id: string; name: string; lat: number | null; lon: number | null; distance_km: number | null; packets: number;
  snr_avg: number; snr_min: number; snr_max: number; rssi_avg: number; rssi_min: number; rssi_max: number; last: number;
}
export interface MeshLinks { days: number; us: { lat: number; lon: number } | null; links: MeshLink[]; farthest: { id: string; name: string; distance_km: number } | null }

export interface Health {
  radio: "meshtastic" | "simulator";
  queue: number;
  knowledge_base: "index" | "demo";
  indexing: string;
  queries: number;
  avg_latency_s: number;
}

/** Linux only: what to try when a USB radio will not connect, worked out from the device files and who we are. */
export interface RadioHelp {
  title: string; why: string; steps: string[]; commands: { label: string; text: string }[]; docker: boolean; open: boolean;
  facts?: Record<string, string | number | number[]>;
}
export interface RadioStatus {
  active: "meshtastic" | "simulator";
  connected: boolean;
  method: string;
  address: string;
  node_id: string;
  error: string | null;
  device: { short_name?: string; long_name?: string; hw?: string };
  help: RadioHelp | null;
  last_rx: number | null;
  rssi: number | null;
  snr: number | null;
}

export interface RadioNode {
  id: string;
  short: string;
  long: string;
  hw?: string;
  snr: number | null;
  last_heard: number | null;
  hops_away: number | null;
  battery: number | null;
  is_self: boolean;
  blocked: boolean;
  allowed: boolean;
}

export interface QueryChunk { title: string; text: string; score: number }
export interface QueryRow {
  id: number;
  ts?: number;
  sender_id: string;
  sender_name: string;
  question: string;
  answer?: string;
  parts?: string[];
  part_count?: number;
  bytes?: number;
  byte_limit: number;
  latency_s: number;
  sources: string[];
  debug: { prompt: string; compressed: boolean; chunks: QueryChunk[]; demo?: boolean; fallback?: boolean };
  /** True when it arrived over the WebSocket just now (drives the typewriter reveal). */
  live?: boolean;
}

export interface Hardware {
  cpu: string; cores: number; ram_gb: number; arch: string; os: string;
  gpu: { name: string; vram_gb: number } | null;
  board: string | null; disk_free_gb: number;
}
export interface ModelSuggestion { tag: string; size_gb: number; blurb: string; recommended: boolean; fits?: boolean }
export interface HardwareInfo {
  hardware: Hardware;
  suggestions: { llm: ModelSuggestion[]; embed: ModelSuggestion[]; speed: string; note: string };
}

export interface InstalledModel { name: string; size_gb: number; params: string }
export interface PullJob { tag: string; state: "running" | "done" | "error"; status: string; completed: number; total: number; error: string | null }

export interface Zim {
  name: string; path: string; title: string; entries: number; articles: number;
  status: string; indexed_articles: number; indexed_chunks: number; last_indexed: number | null; error: string | null;
}
export interface IndexProgress {
  state: "idle" | "running" | "paused" | "done" | "error";
  zim: string; entries_done: number; entries_total: number; articles: number; chunks: number;
  articles_per_s: number; eta_s: number | null; error: string | null;
  params: { max_articles?: number | null; lead_only?: boolean };
  mode?: "all" | "fast"; phase?: "all" | "first" | "rest"; phase_label?: string;
}
export interface KbStats { articles: number; chunks: number; disk_bytes: number; last_indexed: number | null; embedder: string | null }
export interface CatalogEntry { variant: string; filename: string; date: string; size: string; url: string; description: string }
export interface DownloadJob {
  id: string; url: string; filename: string; state: "running" | "paused" | "done" | "error" | "cancelled";
  bytes: number; total: number; speed: number; eta_s: number | null; error: string | null;
}
export interface SearchResult { title: string; section: string; text: string; score: number; detail: Record<string, number | null> }

export interface TryResult {
  parts: string[]; bytes: number[]; byte_limit: number; raw: string; compressed: boolean; fallback?: boolean; latency_s: number;
  chunks: { title: string; section: string; score: number }[];
}
export interface WizardState { done: string[]; skipped: string[]; finished: boolean; step: number }

export type LiveEvent =
  | ({ type: "query" } & QueryRow & { parts: string[] })
  | { type: "rx"; from: string; name: string; dm: boolean; rssi: number | null; snr: number | null }
  | { type: "radio"; status: RadioStatus }
  | { type: "error"; detail: string };

export interface WikiPackage {
  id: string; name: string; variant: string; approx_mb: number; needs_mb?: number; recommended: boolean; blurb: string; good_for: string;
  state: "available" | "downloading" | "paused" | "indexing" | "ready" | "downloaded" | "error";
  progress: number; filename: string | null; articles: number; detail: string | null;
  installed_edition?: string | null; update?: WikiUpdateInfo | null;
}

export interface UpdaterState { state: string; message: string; current?: string | null; target?: string | null; failed_versions?: string[] }
export interface AppUpdateStatus {
  version: string;
  install: { kind: "docker" | "docker-manual" | "git" | "other"; steps: string[] };
  check_enabled: boolean; auto_update: boolean; can_apply: boolean; reason: string | null;
  checked: number | null; last_error: string | null;
  channel: "stable" | "beta";
  latest: { tag: string; url: string; notes: string; beta: boolean } | null; update_available: boolean;
  last_update: { from: string; to: string; ts: number; backup: string; result: string; downgrade?: boolean } | null;
  updater: UpdaterState | null; updater_outdated: boolean;
  on_beta: boolean; can_leave_beta: boolean; leave_beta_steps: string[]; idle_reason: string | null;
  result?: { ok: boolean; outcome: string; message: string };
}
export interface WikiUpdateInfo { package: string; name: string; installed: string; latest: string; size_mb: number; needs_mb: number; replaces: string[] }
export interface WikiUpdateStatus {
  check_enabled: boolean; auto_update: boolean; checked: number | null; error: string | null;
  available: WikiUpdateInfo[];
  last: Record<string, { result: string; to: string; ts: number; message?: string }>;
  result?: { ok: boolean; message: string };
}
