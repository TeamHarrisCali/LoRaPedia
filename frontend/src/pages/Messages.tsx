import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { DeliveryBadge } from "../components/DeliveryBadge";
import { NodePicker } from "../components/NodePicker";
import { Pager } from "../components/Pager";
import { usePaged } from "../lib/usePaged";
import { Badge, Button, Card, EmptyState, Field, PageHeader, SectionTitle } from "../components/ui";
import { api, errorText } from "../lib/api";
import { clockTime, timeAgo, utf8Bytes } from "../lib/format";
import { useLive } from "../lib/live";
import { useToast } from "../lib/toast";
import type { ChatThread, MeshNodes, MessageThread, PublicPosts } from "../lib/types";
import { usePoll } from "../lib/usePoll";

const MAX_BYTES = 233;
const NODE_ID = /^![0-9a-f]{8}$/;

function ByteCount({ text }: { text: string }) {
  const n = utf8Bytes(text);
  return <span className={`text-xs ${n > MAX_BYTES ? "text-danger" : "text-muted"}`} role="status">{n} of {MAX_BYTES} bytes</span>;
}

function Composer({ onSend, busy, placeholder, label }: { onSend: (text: string) => Promise<boolean>; busy: boolean; placeholder: string; label: string }) {
  const [text, setText] = useState("");
  const over = utf8Bytes(text) > MAX_BYTES;
  async function go() { if (await onSend(text)) setText(""); }
  return (
    <div>
      <label className="sr-only" htmlFor="composer">{label}</label>
      <textarea id="composer" className="field min-h-[4.5rem] font-mono text-[13px]" value={text} placeholder={placeholder} rows={3}
        onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && text.trim() && !over && !busy) void go(); }} />
      <div className="mt-2 flex flex-wrap items-center gap-3">
        <Button variant="primary" icon="send" busy={busy} disabled={!text.trim() || over} onClick={() => void go()}>Send</Button>
        <ByteCount text={text} />
        <span className="text-xs text-muted">Ctrl+Enter sends</span>
      </div>
    </div>
  );
}

function Conversation({ id }: { id: string }) {
  const toast = useToast();
  const live = useLive();
  const thread = usePoll(() => api.get<ChatThread>(`/api/messages/thread/${encodeURIComponent(id)}`), 8000, true, [id, live.messageTick, live.deliveryTick]);
  const [busy, setBusy] = useState(false);
  const end = useRef<HTMLDivElement>(null);
  const t = thread.data;
  useEffect(() => { end.current?.scrollIntoView({ block: "nearest" }); }, [t?.messages.length]);

  async function send(text: string) {
    setBusy(true);
    try { await api.post("/api/messages/dm", { node_id: id, text }); await thread.reload(); return true; }
    catch (e) { toast.error(errorText(e)); return false; }
    finally { setBusy(false); }
  }
  async function endChat() {
    try { await api.post(`/api/messages/chat/${encodeURIComponent(id)}/end`); await thread.reload(); toast.success("Chat ended. The AI answers this node again."); }
    catch (e) { toast.error(errorText(e)); }
  }

  return (
    <Card className="flex min-w-0 flex-col">
      <div className="mb-3 flex flex-wrap items-baseline gap-x-3">
        <h2 className="text-xl font-semibold">{t?.name ?? id}</h2>
        <Link className="font-mono text-xs text-muted underline" to={`/nodes/${encodeURIComponent(id)}`}>{id}</Link>
      </div>
      {t?.radio !== undefined && t.radio !== "meshtastic" && (
        <p role="status" className="mb-3 rounded-lg border border-line bg-bg p-3 text-sm">No radio is connected. A message you write here is kept below, marked <b>not sent</b>: <b>nothing is transmitted</b>.</p>
      )}
      {t?.chatting && (
        <div className="mb-3 flex flex-wrap items-center gap-3 rounded-lg border border-amber/50 bg-amber/10 p-3 text-sm" role="status">
          <span>Chat open: what this node sends back (unless it starts with <span className="font-mono">?</span>) comes here and the AI does not answer it, until {t.chat_until ? clockTime(t.chat_until) : "later"}.</span>
          <Button variant="ghost" onClick={() => void endChat()}>End chat</Button>
        </div>
      )}
      <ul className="mb-4 max-h-[28rem] min-h-24 space-y-2 overflow-y-auto" aria-label={`Messages with ${t?.name ?? id}`} aria-live="polite">
        {t && t.messages.length === 0 && <li className="text-sm text-muted">Nothing yet. Write the first message below.</li>}
        {t?.messages.map((m) => (
          <li key={m.id} className={`flex flex-col ${m.direction === "out" ? "items-end" : "items-start"}`}>
            <div className={`max-w-[92%] whitespace-pre-wrap break-words rounded-2xl border px-3.5 py-2 font-mono text-[13px] leading-relaxed ${m.direction === "out" ? "border-amber/60 bg-amber/10" : "border-line bg-raised"}`}>
              <span className="sr-only">{m.direction === "out" ? "You" : t.name}: </span>{m.text}
            </div>
            <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted">
              <span className="font-mono">{clockTime(m.ts)}</span>
              {m.direction === "out" && (m.simulated ? <Badge>not sent: no radio</Badge> : <DeliveryBadge state={m.delivery?.state} detail={m.delivery?.detail} />)}
            </div>
          </li>
        ))}
        <div ref={end} />
      </ul>
      <Composer onSend={send} busy={busy} label={`Message to ${t?.name ?? id}`} placeholder="Write a message. It is sent as a direct message to this node only." />
    </Card>
  );
}

function PublicPostCard() {
  const toast = useToast();
  const live = useLive();
  const info = usePoll(() => api.get<PublicPosts>("/api/messages/public"), 15000, true, [live.messageTick]);
  const [channel, setChannel] = useState(0);
  const [text, setText] = useState("");
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const p = info.data;
  const name = p?.channels.find((c) => c.index === channel)?.name ?? `channel ${channel}`;
  const over = utf8Bytes(text) > MAX_BYTES;

  async function post() {
    setBusy(true); setConfirm(false);
    try { await api.post("/api/messages/public", { text, channel, confirm: true }); setText(""); toast.success(`Posted to ${name}.`); await info.reload(); }
    catch (e) { toast.error(errorText(e)); }
    finally { setBusy(false); }
  }

  return (
    <Card className="mt-5">
      <SectionTitle hint="Something you type yourself, heard by everyone on a channel. LoRaPedia's AI never posts publicly and never sees what you write here.">Post to a public channel</SectionTitle>
      {!p ? <p className="text-sm text-muted">…</p> : !p.enabled ? (
        <p className="text-sm">Posting to a public channel is turned off. <Link className="underline" to="/settings">Turn it on in Settings</Link> if you want it.</p>
      ) : !p.connected ? (
        <p className="text-sm" role="status">No radio is connected, so nothing can be posted.</p>
      ) : (
        <>
          <Field label="Channel" htmlFor="pub-channel">
            <select id="pub-channel" className="field max-w-xs" value={channel} onChange={(e) => { setChannel(Number(e.target.value)); setConfirm(false); }}>
              {p.channels.map((c) => <option key={c.index} value={c.index}>{c.name} (channel {c.index})</option>)}
            </select>
          </Field>
          <label className="sr-only" htmlFor="pub-text">Public post</label>
          <textarea id="pub-text" className="field min-h-[4.5rem] font-mono text-[13px]" rows={3} value={text} placeholder="What do you want to say to the whole channel?"
            onChange={(e) => { setText(e.target.value); setConfirm(false); }} />
          <div className="mt-2 flex flex-wrap items-center gap-3">
            {confirm ? (
              <div className="w-full rounded-lg border border-line bg-bg p-3 text-sm" role="alertdialog" aria-label="Confirm public post">
                <p>Post this to <b>{name}</b>? Everyone on that channel in range will see it, and every node that hears it passes it on. It cannot be taken back. You can post once a minute.</p>
                <p className="mt-2 whitespace-pre-wrap break-words rounded border border-line p-2 font-mono text-[13px]">{text}</p>
                <div className="mt-2 flex gap-2"><Button variant="primary" busy={busy} onClick={() => void post()}>Post to {name}</Button><Button variant="ghost" onClick={() => setConfirm(false)}>Cancel</Button></div>
              </div>
            ) : (
              <>
                <Button variant="primary" icon="send" disabled={!text.trim() || over || busy} onClick={() => setConfirm(true)}>Post…</Button>
                <ByteCount text={text} />
                <span className="text-xs text-muted">Broadcasts are not acknowledged, so there is no delivery report.</span>
              </>
            )}
          </div>
          {p.posts.length > 0 && (
            <ul className="mt-4 space-y-1 text-sm" aria-label="Your recent public posts">
              {p.posts.slice(0, 8).map((m) => (
                <li key={m.id} className="flex flex-wrap gap-x-2"><span className="font-mono text-xs text-muted">{timeAgo(m.ts)}</span><Badge>{p.channels.find((c) => c.index === m.channel)?.name ?? `channel ${m.channel}`}</Badge><span className="break-words">{m.text}</span></li>
              ))}
            </ul>
          )}
        </>
      )}
    </Card>
  );
}

export function Messages() {
  const { id } = useParams();
  const nav = useNavigate();
  const live = useLive();
  const threads = usePoll(() => api.get<MessageThread[]>("/api/messages/threads"), 10000, true, [live.messageTick]);
  const nodes = usePoll(() => api.get<MeshNodes>("/api/mesh/nodes"), 30000);
  const [target, setTarget] = useState("");
  const [search, setSearch] = useState("");
  const list = useMemo(() => (nodes.data?.nodes ?? []).filter((n) => !n.us).sort((a, b) => a.display.localeCompare(b.display)), [nodes.data]);
  const ok = NODE_ID.test(target);
  const found = useMemo(() => {
    const q = search.trim().toLowerCase();
    return (threads.data ?? []).filter((t) => !q || `${t.name} ${t.node_id} ${t.text}`.toLowerCase().includes(q));
  }, [threads.data, search]);
  const paged = usePaged(found, 6, search);

  return (
    <>
      <PageHeader title="Messages" subtitle="Write to a node yourself. These are your own messages: they go to one node as a direct message, and the AI has no part in them." />
      <div className="grid gap-5 lg:grid-cols-[18rem_1fr]">
        <div className="min-w-0">
          <Card>
            <SectionTitle>New message</SectionTitle>
            <Field label="To" htmlFor="msg-node" help={`${list.length} nodes known. Type part of a name, or an id such as !a1b2c3d4.`}>
              <NodePicker id="msg-node" label="Node to write to" allowId value={target} onChange={setTarget}
                nodes={list.map((n) => ({ id: n.id, label: n.display, detail: n.id }))} />
            </Field>
            <Button variant="primary" disabled={!ok} onClick={() => nav(`/messages/${encodeURIComponent(target)}`)}>Open</Button>
          </Card>
          <Card className="mt-5">
            <SectionTitle>Conversations</SectionTitle>
            {threads.data && threads.data.length === 0 && <p className="text-sm text-muted">None yet.</p>}
            {(threads.data?.length ?? 0) > 6 && (
              <input aria-label="Search conversations" type="search" className="field mb-2" placeholder="Search name, id or text…" value={search} onChange={(e) => setSearch(e.target.value)} />
            )}
            {threads.data && threads.data.length > 0 && found.length === 0 && <p className="text-sm text-muted">No conversation matches.</p>}
            <ul className="space-y-1" aria-label="Conversations">
              {paged.slice.map((t) => (
                <li key={t.node_id}>
                  <Link to={`/messages/${encodeURIComponent(t.node_id)}`} aria-current={t.node_id === id ? "page" : undefined}
                    className={`block rounded-lg border px-3 py-2 text-sm ${t.node_id === id ? "border-amber/60 bg-amber/10" : "border-line hover:border-muted"}`}>
                    <span className="flex items-center justify-between gap-2"><span className="truncate font-medium">{t.name}</span>{t.chatting && <Badge tone="amber">chat open</Badge>}</span>
                    <span className="block truncate text-xs text-muted">{t.direction === "out" ? "You: " : ""}{t.text}</span>
                    <span className="block text-xs text-muted">{timeAgo(t.ts)}</span>
                  </Link>
                </li>
              ))}
            </ul>
            <Pager page={paged.page} pages={paged.pages} total={paged.total} size={paged.size} onPage={paged.setPage} noun="conversations" />
          </Card>
        </div>
        <div className="min-w-0">
          {id ? <Conversation key={id} id={id} /> : <EmptyState title="Pick a node to write to">Choose one on the left. After you write to a node, what it sends back comes here instead of going to the AI, for a while.</EmptyState>}
          <PublicPostCard />
        </div>
      </div>
    </>
  );
}
