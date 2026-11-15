import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  Archive,
  ArrowDownToLine,
  Check,
  CheckCheck,
  ChevronDown,
  CircleHelp,
  Clock3,
  Copy,
  Eye,
  File,
  Inbox as InboxIcon,
  LoaderCircle,
  Mail,
  MailOpen,
  MoreHorizontal,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
  Sparkles,
  Trash2,
  X,
  Zap,
} from "lucide-react";
import {
  createInbox,
  deleteMessage,
  downloadAttachment,
  getMessage,
  listMessages,
  type EmailDetail,
  type EmailListItem,
  type InboxSession,
} from "./lib/api";

const STORAGE_KEY = "invismail.inbox.v1";
const TTL_OPTIONS = [
  { label: "15 minutes", value: 900 },
  { label: "1 hour", value: 3600 },
  { label: "3 hours", value: 10800 },
  { label: "24 hours", value: 86400 },
];

function readStoredInbox(): InboxSession | null {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    if (!value) return null;
    const parsed = JSON.parse(value) as InboxSession;
    if (!parsed.access_token || !parsed.email_address || !parsed.expires_at) return null;
    if (new Date(parsed.expires_at).getTime() <= Date.now()) {
      localStorage.removeItem(STORAGE_KEY);
      return null;
    }
    return parsed;
  } catch {
    localStorage.removeItem(STORAGE_KEY);
    return null;
  }
}

function formatTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function formatFullDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function formatRemaining(expiresAt: string, now: number): string {
  const remaining = Math.max(0, Math.floor((new Date(expiresAt).getTime() - now) / 1000));
  const hours = Math.floor(remaining / 3600);
  const minutes = Math.floor((remaining % 3600) / 60);
  const seconds = remaining % 60;
  if (hours) return `${hours}h ${String(minutes).padStart(2, "0")}m`;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

function initials(value: string): string {
  const clean = value.split("@")[0]?.replace(/[^a-z0-9]/gi, "") ?? "M";
  return clean.slice(0, 1).toUpperCase() || "M";
}

function prettySize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function App() {
  const [inbox, setInbox] = useState<InboxSession | null>(() => readStoredInbox());
  const createStarted = useRef(false);
  const [ttl, setTtl] = useState(3600);
  const [messages, setMessages] = useState<EmailListItem[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedMessage, setSelectedMessage] = useState<EmailDetail | null>(null);
  const [loadingInbox, setLoadingInbox] = useState(!readStoredInbox());
  const [loadingMessages, setLoadingMessages] = useState(false);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [creating, setCreating] = useState(false);
  const [search, setSearch] = useState("");
  const [now, setNow] = useState(Date.now());
  const [copied, setCopied] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [mobileView, setMobileView] = useState<"list" | "message">("list");
  const refreshInFlight = useRef(false);
  const firstLoad = useRef(true);

  const remaining = inbox ? formatRemaining(inbox.expires_at, now) : "--:--";
  const isExpired = inbox ? new Date(inbox.expires_at).getTime() <= now : false;
  const unreadCount = messages.filter((message) => !message.is_read).length;

  const filteredMessages = useMemo(() => {
    const term = search.trim().toLowerCase();
    if (!term) return messages;
    return messages.filter((message) =>
      [message.sender, message.subject].some((value) => value.toLowerCase().includes(term)),
    );
  }, [messages, search]);

  const createNewInbox = useCallback(async (nextTtl = ttl) => {
    setCreating(true);
    setError("");
    setNotice("");
    try {
      const created = {
        ...(await createInbox(nextTtl)),
        created_at: new Date().toISOString(),
        ttl_seconds: nextTtl,
      };
      localStorage.setItem(STORAGE_KEY, JSON.stringify(created));
      setInbox(created);
      setMessages([]);
      setSelectedId(null);
      setSelectedMessage(null);
      setMobileView("list");
      return created;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create an inbox.");
      return null;
    } finally {
      setCreating(false);
      setLoadingInbox(false);
    }
  }, [ttl]);

  const refreshMessages = useCallback(async (quiet = false) => {
    if (!inbox || refreshInFlight.current || isExpired) return;
    refreshInFlight.current = true;
    if (!quiet) setLoadingMessages(true);
    try {
      const response = await listMessages(inbox.access_token);
      setMessages(response.items);
      setError("");
      if (response.items.length > 0) {
        setSelectedId((current) => current ?? response.items[0].id);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to refresh this inbox.");
    } finally {
      refreshInFlight.current = false;
      setLoadingMessages(false);
    }
  }, [inbox, isExpired]);

  useEffect(() => {
    if (!inbox && !createStarted.current) {
      createStarted.current = true;
      void createNewInbox(ttl);
    } else if (inbox) {
      setLoadingInbox(false);
    }
    // Restore or create the browser's current inbox once on mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    if (!inbox || isExpired) return;
    void refreshMessages(firstLoad.current ? false : true);
    firstLoad.current = false;
    const poll = window.setInterval(() => void refreshMessages(true), 5000);
    return () => window.clearInterval(poll);
  }, [inbox, isExpired, refreshMessages]);

  useEffect(() => {
    if (!inbox || !selectedId) {
      setSelectedMessage(null);
      return;
    }
    let cancelled = false;
    setLoadingDetail(true);
    getMessage(inbox.access_token, selectedId)
      .then((message) => {
        if (!cancelled) {
          setSelectedMessage(message);
          setMessages((current) =>
            current.map((item) => item.id === message.id ? { ...item, is_read: true } : item),
          );
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setSelectedMessage(null);
          setError(err instanceof Error ? err.message : "Unable to open this message.");
        }
      })
      .finally(() => {
        if (!cancelled) setLoadingDetail(false);
      });
    return () => { cancelled = true; };
  }, [inbox, selectedId]);

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(""), 2400);
    return () => window.clearTimeout(timer);
  }, [notice]);

  const copyAddress = async () => {
    if (!inbox) return;
    try {
      await navigator.clipboard.writeText(inbox.email_address);
      setCopied(true);
      setNotice("Address copied to clipboard");
      window.setTimeout(() => setCopied(false), 1800);
    } catch {
      setError("Clipboard access was blocked by your browser.");
    }
  };

  const handleDelete = async () => {
    if (!inbox || !selectedMessage) return;
    try {
      await deleteMessage(inbox.access_token, selectedMessage.id);
      setMessages((current) => current.filter((item) => item.id !== selectedMessage.id));
      setSelectedMessage(null);
      setSelectedId(null);
      setNotice("Message deleted");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not delete the message.");
    }
  };

  const handleDownload = async (attachmentId: string, filename: string) => {
    if (!inbox) return;
    try {
      const blob = await downloadAttachment(inbox.access_token, attachmentId);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Attachment download failed.");
    }
  };

  const handleNewAddress = async () => {
    firstLoad.current = true;
    await createNewInbox(ttl);
  };

  const htmlDocument = selectedMessage?.html_body
    ? `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data: cid:; style-src 'unsafe-inline'; font-src data:;"><style>body{font-family:Inter,ui-sans-serif,system-ui,-apple-system,sans-serif;color:#e8ebe4;background:#fff;padding:22px;line-height:1.6;overflow-wrap:anywhere}img{max-width:100%;height:auto}a{color:#477b24}</style></head><body>${selectedMessage.html_body}</body></html>`
    : "";

  return (
    <div className="app-shell">
      <div className="ambient ambient-one" />
      <div className="ambient ambient-two" />
      <header className="topbar">
        <a className="brand" href="/" aria-label="InvisMail home">
          <img src="/invismail-mark.svg" alt="" className="brand-mark" />
          <span className="brand-name"><span>Invis</span>Mail</span>
          <span className="brand-beta">BETA</span>
        </a>
        <div className="topbar-right">
          <span className="system-status"><span className="status-dot" /> System operational</span>
          <a className="top-link" href="https://github.com/S-Sanjai/invismail" target="_blank" rel="noreferrer">Open source <span>↗</span></a>
          <button className="icon-button help-button" title="About InvisMail" onClick={() => setNotice("A temporary inbox that stays yours for a little while.")}>
            <CircleHelp size={17} />
          </button>
        </div>
      </header>

      <main className="workspace">
        <aside className="sidebar">
          <div className="sidebar-section-label">WORKSPACE</div>
          <button className="side-nav active"><InboxIcon size={17} /> <span>Inbox</span><span className="nav-count">{messages.length}</span></button>
          <div className="sidebar-spacer" />
          <div className="privacy-card">
            <div className="privacy-icon"><Eye size={17} /></div>
            <div className="privacy-title">Less exposure. More control.</div>
            <p>Your temporary address keeps your personal inbox out of the loop.</p>
            <div className="privacy-rule" />
            <div className="privacy-foot"><ShieldCheck size={13} /> Private by design</div>
          </div>
          <div className="sidebar-footer">
            <span className="sidebar-footer-dot" /> Personal workspace
          </div>
        </aside>

        <section className="main-panel">
          <div className="page-heading">
            <div>
              <div className="eyebrow"><span className="eyebrow-line" /> TEMPORARY EMAIL, WITHOUT THE NOISE</div>
              <h1>Your quiet corner<br /><span>of the internet.</span></h1>
              <p className="intro-copy">A disposable inbox for the things that don't need your real address.</p>
            </div>
            <div className="heading-note"><span className="heading-note-icon"><Sparkles size={15} /></span><span>Ready when you are.<br /><strong>Gone when time's up.</strong></span></div>
          </div>

          <section className="inbox-card">
            <div className="inbox-toolbar">
              <div className="address-block">
                <div className="field-label">YOUR TEMPORARY ADDRESS</div>
                <div className="address-line">
                  <span className="address-at">@</span>
                  <span className="address-value">{loadingInbox ? "Creating your private address..." : inbox?.email_address ?? "Inbox unavailable"}</span>
                  <button className="copy-button" onClick={() => void copyAddress()} disabled={!inbox} aria-label="Copy email address">
                    {copied ? <Check size={15} /> : <Copy size={15} />}
                    <span>{copied ? "Copied" : "Copy address"}</span>
                  </button>
                </div>
              </div>
              <div className="toolbar-actions">
                <label className="ttl-control">
                  <Clock3 size={14} />
                  <span>Expires in</span>
                  <strong>{remaining}</strong>
                  <select value={ttl} onChange={(event) => setTtl(Number(event.target.value))} aria-label="Lifetime for the next address" title="Applied when you create the next address">
                    {TTL_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                  </select>
                </label>
                <button className="text-action" onClick={() => void handleNewAddress()} disabled={creating}>
                  {creating ? <LoaderCircle className="spin" size={14} /> : <Plus size={14} />}
                  New address
                </button>
              </div>
              <div className="expiry-track"><span style={{ width: inbox ? `${Math.max(2, Math.min(100, ((new Date(inbox.expires_at).getTime() - now) / ((inbox as InboxSession & { ttl_seconds?: number }).ttl_seconds ?? ttl) / 1000) * 100))}%` : "0%" }} /></div>
            </div>

            <div className="mail-layout">
              <section className={`message-list-pane ${mobileView === "message" ? "mobile-hidden" : ""}`}>
                <div className="list-header">
                  <div className="list-title">Inbox <span className="count-pill">{messages.length}</span></div>
                  <button className={`icon-button refresh-button ${loadingMessages ? "is-refreshing" : ""}`} onClick={() => void refreshMessages()} disabled={!inbox || loadingMessages} title="Refresh inbox">
                    <RefreshCw size={15} />
                  </button>
                </div>
                <div className="search-box">
                  <Search size={15} />
                  <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search your inbox" aria-label="Search your inbox" />
                  {search && <button onClick={() => setSearch("")} aria-label="Clear search"><X size={13} /></button>}
                </div>
                <div className="message-list">
                  {loadingInbox ? (
                    <div className="list-loading"><LoaderCircle size={18} className="spin" /> Preparing your inbox</div>
                  ) : filteredMessages.length === 0 ? (
                    <div className="empty-list">
                      <div className="empty-mail-icon"><MailOpen size={21} /></div>
                      <strong>{search ? "No matching messages" : "Nothing in here. Yet."}</strong>
                      <p>{search ? "Try a different sender or subject." : "Your inbox is listening. New messages will appear here automatically."}</p>
                    </div>
                  ) : filteredMessages.map((message) => (
                    <button
                      className={`message-row ${selectedId === message.id ? "selected" : ""} ${!message.is_read ? "unread" : ""}`}
                      key={message.id}
                      onClick={() => { setSelectedId(message.id); setMobileView("message"); }}
                    >
                      <span className="sender-avatar">{initials(message.sender)}</span>
                      <span className="message-row-content">
                        <span className="message-row-top"><strong>{message.sender}</strong><time>{formatTime(message.received_at)}</time></span>
                        <span className="message-subject">{message.subject || "(No subject)"}</span>
                        <span className="message-preview">{message.is_read ? "Open message" : "New message"}</span>
                      </span>
                      {!message.is_read && <span className="unread-dot" />}
                    </button>
                  ))}
                </div>
                <div className="list-bottom"><span><span className="live-dot" /> Updates automatically</span><span>{unreadCount} unread</span></div>
              </section>

              <section className={`reading-pane ${mobileView === "list" ? "mobile-hidden" : ""}`}>
                {!selectedId ? (
                  <div className="reading-empty">
                    <div className="empty-illustration"><Mail size={25} /></div>
                    <h2>Your inbox is ready.</h2>
                    <p>Send an email to your temporary address. It will show up here as soon as it arrives.</p>
                    <div className="empty-hint"><Zap size={15} /> No refresh ritual required.</div>
                  </div>
                ) : loadingDetail ? (
                  <div className="reading-empty"><LoaderCircle className="spin" size={22} /><p>Opening message…</p></div>
                ) : selectedMessage ? (
                  <article className="email-detail">
                    <div className="detail-toolbar">
                      <button className="back-button" onClick={() => setMobileView("list")}><ChevronDown size={15} /> Back to inbox</button>
                      <div className="detail-actions">
                        <button className="icon-button" title="Delete message" onClick={() => void handleDelete()}><Trash2 size={16} /></button>
                        <button className="icon-button" title="More actions" onClick={() => setNotice("Message received " + formatFullDate(selectedMessage.received_at))}><MoreHorizontal size={17} /></button>
                      </div>
                    </div>
                    <div className="email-content">
                      <div className="detail-category"><span className="category-dot" /> RECEIVED MESSAGE</div>
                      <h2 className="email-subject">{selectedMessage.subject || "(No subject)"}</h2>
                      <div className="sender-block">
                        <div className="sender-avatar large">{initials(selectedMessage.sender)}</div>
                        <div className="sender-meta"><strong>{selectedMessage.sender}</strong><span>To: {selectedMessage.recipient}</span></div>
                        <time>{formatFullDate(selectedMessage.received_at)}</time>
                      </div>
                      <div className="email-body">
                        {selectedMessage.html_body ? (
                          <iframe
                            className="html-email-frame"
                            title="Email content"
                            sandbox=""
                            referrerPolicy="no-referrer"
                            srcDoc={htmlDocument}
                          />
                        ) : selectedMessage.text_body ? (
                          <pre className="plain-email">{selectedMessage.text_body}</pre>
                        ) : (
                          <div className="no-body">This message doesn't contain a text body.</div>
                        )}
                      </div>
                      {selectedMessage.attachments.length > 0 && (
                        <div className="attachments">
                          <div className="attachments-heading"><span>Attachments</span><span>{selectedMessage.attachments.length} file(s)</span></div>
                          {selectedMessage.attachments.map((attachment) => (
                            <button key={attachment.id} className="attachment-row" onClick={() => void handleDownload(attachment.id, attachment.filename)}>
                              <span className="attachment-icon"><File size={17} /></span>
                              <span className="attachment-name"><strong>{attachment.filename}</strong><small>{prettySize(attachment.size)} · {attachment.content_type}</small></span>
                              <ArrowDownToLine size={16} />
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                  </article>
                ) : (
                  <div className="reading-empty"><AlertCircle size={22} /><h2>Couldn't open this message.</h2><p>Refresh the inbox and try again.</p></div>
                )}
              </section>
            </div>
          </section>

          <footer className="page-footer">
            <div><ShieldCheck size={14} /> Your inbox, your key</div>
            <div><Clock3 size={14} /> Expires automatically</div>
            <div><Archive size={14} /> Messages are temporary</div>
            <span>Built to disappear.</span>
          </footer>
        </section>
      </main>

      {(error || notice) && (
        <div className={`toast ${error ? "toast-error" : ""}`} role="status">
          {error ? <AlertCircle size={16} /> : <CheckCheck size={16} />}
          <span>{error || notice}</span>
          <button onClick={() => { setError(""); setNotice(""); }} aria-label="Dismiss"><X size={14} /></button>
        </div>
      )}
    </div>
  );
}

export default App;
