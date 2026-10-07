/** Trader Community (master upgrade §6) - feed, compose, detail.
 *  Privacy: display names only. Verified trade results come frozen from the
 *  backend (broker-confirmed signals) and can never be edited after the fact.
 */
import { useEffect, useRef, useState } from "react";
import { Users, Image as ImageIcon, Heart, MessageCircle, Flag, Send, Trash2, BadgeCheck, UserPlus } from "lucide-react";
import { api } from "../lib/api";
import { usePolling } from "../lib/usePolling";
import { Eyebrow, Glass } from "../components/ui";

type Post = {
  id: string; author: { user_id: string; display_name: string };
  text: string; image?: string | null; tags: string[];
  trade?: { market: string; direction: string; entry: number; sl: number;
            tp1?: number; status: string; pl?: number; r_multiple?: number;
            outcome?: string; verified: boolean } | null;
  likes: number; liked_by_me: boolean; comment_count: number;
  comments: { id: string; author: { display_name: string }; text: string; at: string }[];
  hidden?: boolean; createdAt: string;
};

const TAG_IDEAS = ["XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "Strategy 2", "9/21 EMA"];

export function CommunityScreen() {
  const feed = usePolling<{ posts: Post[]; next_before: string | null }>(
    () => api.get("/api/community/posts?limit=30"), 30000);
  const me = usePolling<{ id: string }>(() => api.get("/api/auth/me"), 60000);
  const [text, setText] = useState("");
  const [tags, setTags] = useState<string[]>([]);
  const [image, setImage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [flash, setFlash] = useState<string | null>(null);
  const [open, setOpen] = useState<Post | null>(null);
  const [comment, setComment] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  const post = async () => {
    if (!text.trim()) return;
    setBusy(true); setFlash(null);
    try {
      await api.post("/api/community/posts", { text: text.trim(), tags, image });
      setText(""); setTags([]); setImage(null);
      setFlash("Posted ✓"); feed.refresh();
    } catch (e: any) { setFlash(e?.message || "could not post"); }
    setBusy(false);
  };

  const pickImage = (f: File | null) => {
    if (!f) return;
    if (f.size > 220_000) { setFlash("image too large (max ~200 KB) - screenshot smaller or compress"); return; }
    const rd = new FileReader();
    rd.onload = () => setImage(String(rd.result));
    rd.readAsDataURL(f);
  };

  const like = async (p: Post) => {
    const r = await api.post<{ likes: number; liked_by_me: boolean }>(`/api/community/posts/${p.id}/like`, {});
    feed.refresh();
    if (open?.id === p.id) setOpen({ ...open, likes: r.likes, liked_by_me: r.liked_by_me });
  };

  const report = async (p: Post) => {
    await api.post(`/api/community/posts/${p.id}/report`, { reason: "reported from app" });
    setFlash("Reported to moderation - thank you"); feed.refresh(); setOpen(null);
  };

  const follow = async (uid: string) => {
    const r = await api.post<{ following_count: number }>(`/api/community/follow/${uid}`, {});
    setFlash(`Following list updated (${r.following_count})`);
  };

  const addComment = async (p: Post) => {
    if (!comment.trim()) return;
    const r = await api.post<{ comments: Post["comments"]; comment_count: number }>(
      `/api/community/posts/${p.id}/comments`, { text: comment.trim() });
    setOpen({ ...open!, comments: r.comments, comment_count: r.comment_count });
    setComment(""); feed.refresh();
  };

  const delComment = async (p: Post, cid: string) => {
    const r = await api.del<{ comments: Post["comments"]; comment_count: number }>(
      `/api/community/posts/${p.id}/comments/${cid}`);
    setOpen({ ...open!, comments: r.comments, comment_count: r.comment_count });
    feed.refresh();
  };

  return (
    <div className="animate-fadeUp">
      <header className="mb-5 flex items-start justify-between">
        <div className="flex items-center gap-3.5">
          <span className="icon-chip" aria-hidden="true"><Users size={20} /></span>
          <div>
            <h1 className="text-[22px] font-semibold tracking-tight">Community</h1>
            <p className="mt-1 text-[12.5px] text-txt-low">Ideas, setups & verified results</p>
          </div>
        </div>
      </header>

      <Glass>
        <Eyebrow>Share with the traders</Eyebrow>
        <textarea value={text} onChange={(e) => setText(e.target.value)}
          rows={3} maxLength={2000}
          placeholder="Analysis, a setup you're watching, a lesson…"
          className="mt-2 w-full rounded-2xl border border-[rgba(var(--p-rgb),0.25)] bg-[rgba(var(--p-rgb),0.06)] px-3.5 py-2.5 text-[12.5px] text-txt-hi placeholder:text-txt-faint focus:outline-none" />
        <div className="mt-2.5 flex flex-wrap gap-1.5">
          {TAG_IDEAS.map((t) => (
            <button key={t} onClick={() => setTags((p) => p.includes(t) ? p.filter((x) => x !== t) : [...p, t])}
              aria-pressed={tags.includes(t)}
              className={`tap rounded-lg border px-2.5 py-1 text-[10px] font-bold ${tags.includes(t) ? "chip-on" : "chip-off"}`}>
              {t}
            </button>
          ))}
        </div>
        {image && (
          <div className="mt-2.5 flex items-center gap-2.5">
            <img src={image} alt="attachment" className="h-14 w-14 rounded-xl object-cover" />
            <button onClick={() => setImage(null)} className="text-[11px] text-neg">remove</button>
          </div>
        )}
        <div className="mt-3 flex items-center gap-2.5">
          <input ref={fileRef} type="file" accept="image/png,image/jpeg,image/webp" className="hidden"
            onChange={(e) => pickImage(e.target.files?.[0] || null)} />
          <button onClick={() => fileRef.current?.click()} className="btn-ghost flex items-center gap-1.5 !px-3 !py-2 text-[11px]">
            <ImageIcon size={13} /> Chart image
          </button>
          <button onClick={post} disabled={busy || !text.trim()}
            className="btn-primary flex flex-1 items-center justify-center gap-2 !py-2.5 text-[12px]">
            <Send size={13} /> {busy ? "Posting…" : "Post"}
          </button>
        </div>
        {flash && <p className="mt-2 text-[11px] text-txt-mid">{flash}</p>}
      </Glass>

      <div className="mt-5 space-y-3">
        {(feed.data?.posts || []).map((p) => (
          <Glass key={p.id}>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2.5">
                <span className="grid h-8 w-8 place-items-center rounded-full bg-[rgba(var(--p-rgb),0.15)] text-[11px] font-bold">
                  {p.author.display_name.slice(0, 2).toUpperCase()}
                </span>
                <div>
                  <p className="text-[12px] font-bold">{p.author.display_name}</p>
                  <p className="text-[9.5px] text-txt-faint">{new Date(p.createdAt).toLocaleString()}</p>
                </div>
              </div>
              {me.data && me.data.id !== p.author.user_id && (
                <button onClick={() => follow(p.author.user_id)} className="flex items-center gap-1 text-[10.5px] font-bold text-[var(--accent-cyan)]">
                  <UserPlus size={12} /> Follow
                </button>
              )}
            </div>
            <p className="mt-2.5 whitespace-pre-wrap text-[12.5px] leading-relaxed text-txt-hi">{p.text}</p>
            {p.image && <img src={p.image} alt="chart" className="mt-2.5 max-h-64 w-full rounded-2xl object-contain" />}
            {p.trade && (
              <div className="mt-2.5 rounded-2xl border border-[rgba(var(--p-rgb),0.25)] bg-[rgba(var(--p-rgb),0.06)] p-3">
                <p className="flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-wide text-[var(--accent-cyan)]">
                  <BadgeCheck size={12} /> Verified trade result
                </p>
                <p className="mt-1 text-[12px] font-bold">
                  <span className={p.trade.direction === "BUY" ? "text-pos" : "text-neg"}>{p.trade.direction}</span>{" "}
                  {p.trade.market} @ {p.trade.entry} · {p.trade.status}
                </p>
                <p className="text-[11px] text-txt-mid">
                  SL {p.trade.sl}{p.trade.tp1 ? ` · TP1 ${p.trade.tp1}` : ""}
                  {p.trade.pl != null && <> · P/L <span className={p.trade.pl >= 0 ? "text-pos" : "text-neg"}>${p.trade.pl.toFixed(2)}</span></>}
                  {p.trade.r_multiple != null && <> · {p.trade.r_multiple}R</>}
                </p>
              </div>
            )}
            {p.tags.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1.5">
                {p.tags.map((t) => <span key={t} className="rounded-lg bg-[rgba(var(--warm-rgb),0.1)] px-2 py-0.5 text-[9.5px] font-bold text-txt-mid">{t}</span>)}
              </div>
            )}
            <div className="mt-3 flex items-center gap-4 border-t border-[rgba(var(--warm-rgb),0.07)] pt-2.5">
              <button onClick={() => like(p)} className={`flex items-center gap-1.5 text-[11px] font-bold ${p.liked_by_me ? "text-pos" : "text-txt-mid"}`}>
                <Heart size={13} fill={p.liked_by_me ? "currentColor" : "none"} /> {p.likes}
              </button>
              <button onClick={() => setOpen(open?.id === p.id ? null : p)} className="flex items-center gap-1.5 text-[11px] font-bold text-txt-mid">
                <MessageCircle size={13} /> {p.comment_count}
              </button>
              <button onClick={() => report(p)} className="ml-auto text-txt-faint"><Flag size={12} /></button>
            </div>
            {open?.id === p.id && (
              <div className="mt-2.5 space-y-2 border-t border-[rgba(var(--warm-rgb),0.07)] pt-2.5">
                {p.comments.map((cm) => (
                  <div key={cm.id} className="flex items-start justify-between gap-2">
                    <p className="text-[11.5px] text-txt-mid"><span className="font-bold text-txt-hi">{cm.author.display_name}:</span> {cm.text}</p>
                    {me.data && me.data && (
                      <button onClick={() => delComment(p, cm.id)} className="text-txt-faint"><Trash2 size={11} /></button>
                    )}
                  </div>
                ))}
                <div className="flex gap-2">
                  <input value={comment} onChange={(e) => setComment(e.target.value)} maxLength={500}
                    placeholder="Write a reply…"
                    className="min-w-0 flex-1 rounded-xl border border-[rgba(var(--p-rgb),0.25)] bg-[rgba(var(--p-rgb),0.06)] px-3 py-2 text-[11.5px] focus:outline-none" />
                  <button onClick={() => addComment(p)} className="btn-primary !px-3 !py-2"><Send size={12} /></button>
                </div>
              </div>
            )}
          </Glass>
        ))}
        {feed.data && feed.data.posts.length === 0 && (
          <Glass><p className="py-6 text-center text-[12px] text-txt-mid">No posts yet - be the first to share a setup.</p></Glass>
        )}
      </div>
    </div>
  );
}
