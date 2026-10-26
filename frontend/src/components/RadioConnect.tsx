import { useEffect, useState } from "react";
import { api, errorText } from "../lib/api";
import { useSettings } from "../lib/settings";
import { useToast } from "../lib/toast";
import type { RadioStatus, Settings } from "../lib/types";
import { usePoll } from "../lib/usePoll";
import { SignalGlyph } from "./SignalGlyph";
import { Button, Dot, Field, Help } from "./ui";

type Method = Settings["radio_method"];
const METHODS: [Method, string, string][] = [
  ["auto", "Automatic (USB)", "Uses the radio if exactly one is plugged in. Falls back to the simulator."],
  ["serial", "USB, choose the port", "Pick a specific serial port, e.g. if several radios are attached."],
  ["tcp", "Network (WiFi or Ethernet)", "For radios with WiFi enabled. Enter its IP address."],
  ["ble", "Bluetooth", "Pair by scanning. Range is short; best for testing."],
  ["simulator", "Simulator (no radio)", "Type as a fake node from the Dashboard. Nothing goes over the air."],
];

/** Connection picker used on the Radio page and in the setup wizard. Applies immediately. */
export function RadioConnect({ status, onChanged }: { status: RadioStatus | null | undefined; onChanged?: () => void }) {
  const { settings, save, reload } = useSettings();
  const toast = useToast();
  const [method, setMethod] = useState<Method>("auto");
  const [address, setAddress] = useState("");
  const [busy, setBusy] = useState(false);
  const [ble, setBle] = useState<{ name: string; address: string }[] | null>(null);
  const [scanning, setScanning] = useState(false);
  const ports = usePoll(() => api.get<{ serial: string[] }>("/api/radio/ports"), 5000, method === "serial" || method === "auto");

  const loaded = settings !== null;
  useEffect(() => { if (settings) { setMethod(settings.radio_method); setAddress(settings.radio_address); } }, [loaded]); // eslint-disable-line react-hooks/exhaustive-deps

  async function useSimulator() {
    try { await api.post("/api/radio/use-simulator"); await reload(); setMethod("simulator"); setAddress(""); toast.success("Using the simulator."); onChanged?.(); }
    catch (e) { toast.error(errorText(e)); }
  }

  async function connect() {
    if (!settings) return;
    setBusy(true);
    try {
      if (!(await save({ ...settings, radio_method: method, radio_address: method === "auto" || method === "simulator" ? "" : address }))) return;
      const result = await api.post<RadioStatus>("/api/radio/retry");
      if (result.connected) toast.success(`Radio connected: ${result.device.short_name ?? result.node_id}.`);
      else if (method === "simulator") toast.success("Using the simulator.");
      else toast.error(result.error ?? "Could not connect to a radio.", [{ label: "Retry", run: () => void connect() }, { label: "Use simulator", run: () => void useSimulator() }]);
      onChanged?.();
    } catch (e) { toast.error(errorText(e)); } finally { setBusy(false); }
  }

  async function scan() {
    setScanning(true);
    try { setBle(await api.get<{ name: string; address: string }[]>("/api/radio/ble-scan")); }
    catch (e) { toast.error(errorText(e)); } finally { setScanning(false); }
  }

  const connected = status?.connected;
  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-line bg-bg p-3" role="status">
        <Dot tone={connected ? "teal" : status?.error ? "danger" : "amber"} label={connected ? "Connected" : "Not connected"} />
        {connected ? (
          <span className="text-sm"><b>{status?.device.short_name ?? status?.node_id}</b> <span className="font-mono text-xs text-muted">{status?.node_id} via {status?.address}</span></span>
        ) : (
          <span className="text-sm">{status?.error ? <span className="text-danger">{status.error}</span> : "Using the simulator (no radio attached)."}</span>
        )}
        {connected && <span className="flex items-center gap-1.5 text-sm text-muted"><SignalGlyph snr={status?.snr} />{status?.rssi != null ? `${status.rssi} dBm` : "waiting for a packet"}</span>}
        <span className="ml-auto flex gap-2">
          <Button icon="refresh" onClick={() => void connect()} busy={busy}>Retry</Button>
          {!(status?.method === "simulator") && <Button variant="ghost" onClick={() => void useSimulator()}>Use simulator</Button>}
        </span>
      </div>

      <fieldset className="mb-4">
        <legend className="mb-2 text-sm font-medium">Connection method</legend>
        {METHODS.map(([v, label, help]) => (
          <label key={v} className="mb-2 flex cursor-pointer items-start gap-3">
            <input type="radio" name="radio-method" className="mt-1" checked={method === v} onChange={() => { setMethod(v); setAddress(""); }} />
            <span><span className="text-sm font-medium">{label}</span><Help>{help}</Help></span>
          </label>
        ))}
      </fieldset>

      {method === "serial" && (
        <Field label="Serial port" htmlFor="serial" help={ports.data?.serial.length ? "Detected Meshtastic devices." : "No device detected. Check the USB cable carries data, then press Refresh."}>
          <div className="flex gap-2">
            <select id="serial" className="field" value={address} onChange={(e) => setAddress(e.target.value)}>
              <option value="">Select a port…</option>
              {ports.data?.serial.map((p) => <option key={p} value={p}>{p}</option>)}
              {address && !ports.data?.serial.includes(address) && <option value={address}>{address}</option>}
            </select>
            <Button icon="refresh" onClick={() => void ports.reload()} aria-label="Refresh ports" />
          </div>
        </Field>
      )}
      {method === "tcp" && (
        <Field label="Radio address" htmlFor="tcp" help="IP address or hostname, e.g. 192.168.1.50 or meshtastic.local.">
          <input id="tcp" className="field font-mono" placeholder="192.168.1.50" value={address} onChange={(e) => setAddress(e.target.value)} />
        </Field>
      )}
      {method === "ble" && (
        <Field label="Bluetooth device" htmlFor="ble" help="Press Scan with the radio nearby and awake.">
          <div className="flex gap-2">
            <select id="ble" className="field" value={address} onChange={(e) => setAddress(e.target.value)}>
              <option value="">Select a device…</option>
              {ble?.map((d) => <option key={d.address} value={d.address}>{d.name} ({d.address})</option>)}
              {address && !ble?.some((d) => d.address === address) && <option value={address}>{address}</option>}
            </select>
            <Button onClick={() => void scan()} busy={scanning}>Scan</Button>
          </div>
        </Field>
      )}
      <Button variant="primary" onClick={() => void connect()} busy={busy}>{method === "simulator" ? "Use simulator" : "Connect"}</Button>
    </div>
  );
}
