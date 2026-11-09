"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { supabase } from "@/lib/supabase";
import { discordUrl, useApp } from "./Providers";

export default function ChatWidget() {
  const { user, isAdmin, site, authReady } = useApp();
  const [open, setOpen] = useState(false);
  const [chats, setChats] = useState([]);
  const [names, setNames] = useState({});
  const [threadId, setThreadId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const bodyRef = useRef(null);

  // Customers always talk in their own thread.
  const activeThread = isAdmin ? threadId : user?.id || null;

  // Admin inbox
  useEffect(() => {
    if (!isAdmin) return;
    let cancelled = false;
    const load = async () => {
      const { data } = await supabase.from("chats").select("*").order("updated_at", { ascending: false }).limit(100);
      if (cancelled || !data) return;
      setChats(data);
      const ids = data.map((chat) => chat.user_id);
      if (ids.length) {
        const { data: people } = await supabase.from("profiles").select("id, username").in("id", ids);
        if (!cancelled && people) setNames(Object.fromEntries(people.map((p) => [p.id, p.username])));
      }
    };
    load();
    const channel = supabase
      .channel("admin-chats")
      .on("postgres_changes", { event: "*", schema: "public", table: "chats" }, load)
      .subscribe();
    return () => {
      cancelled = true;
      supabase.removeChannel(channel);
    };
  }, [isAdmin]);

  // Messages in the open thread
  useEffect(() => {
    if (!activeThread || !open) return;
    let cancelled = false;
    supabase
      .from("chat_messages")
      .select("*")
      .eq("user_id", activeThread)
      .order("created_at", { ascending: true })
      .limit(300)
      .then(({ data }) => {
        if (!cancelled && data) setMessages(data);
      });
    const channel = supabase
      .channel("chat-" + activeThread)
      .on(
        "postgres_changes",
        { event: "INSERT", schema: "public", table: "chat_messages", filter: "user_id=eq." + activeThread },
        (payload) => setMessages((current) => (current.some((m) => m.id === payload.new.id) ? current : [...current, payload.new]))
      )
      .subscribe();
    if (isAdmin) supabase.from("chats").update({ unread: false }).eq("user_id", activeThread).then(() => {});
    return () => {
      cancelled = true;
      supabase.removeChannel(channel);
    };
  }, [activeThread, open, isAdmin]);

  useEffect(() => {
    if (bodyRef.current) bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
  }, [messages]);

  async function send(event) {
    event.preventDefault();
    const body = text.trim();
    if (!body || !activeThread) return;
    setText("");
    setError("");
    const { data, error: sendError } = await supabase
      .from("chat_messages")
      .insert({ user_id: activeThread, sender: isAdmin ? "admin" : "customer", body })
      .select()
      .single();
    if (sendError) {
      setText(body);
      setError("That message didn't send. Please try again.");
      return;
    }
    setMessages((current) => (current.some((m) => m.id === data.id) ? current : [...current, data]));
  }

  const unread = isAdmin && chats.some((chat) => chat.unread);
  const mine = isAdmin ? "admin" : "customer";

  let content;
  if (!authReady) {
    content = <div className="bubble info">Loading…</div>;
  } else if (!user) {
    content = (
      <div className="bubble info">
        <Link href="/login">Log in</Link> or <Link href="/signup">sign up</Link> to chat with us, or message us on{" "}
        <a href={discordUrl(site)} target="_blank" rel="noopener noreferrer">Discord</a>.
      </div>
    );
  } else if (isAdmin && !threadId) {
    content = chats.length ? (
      chats.map((chat) => (
        <button key={chat.user_id} type="button" className={chat.unread ? "inbox-row new" : "inbox-row"} onClick={() => setThreadId(chat.user_id)}>
          <strong>{names[chat.user_id] || "Customer"}</strong>
          <span>{chat.last_text}</span>
        </button>
      ))
    ) : (
      <div className="bubble info">Customer chats will appear here.</div>
    );
  } else if (!messages.length) {
    content = <div className="bubble info">{isAdmin ? "No messages yet." : "Send a message and we'll reply here."}</div>;
  } else {
    content = messages.map((message) => (
      <div key={message.id} className={message.sender === mine ? "bubble mine" : "bubble"}>{message.body}</div>
    ));
  }

  return (
    <>
      {open && (
        <section className="chat" aria-label="Live chat">
          <div className="chat-head">
            {isAdmin && threadId ? (
              <button className="icon-button" type="button" onClick={() => { setThreadId(null); setMessages([]); }}>Back</button>
            ) : null}
            <strong>{isAdmin ? (threadId ? names[threadId] || "Customer" : "Customer chats") : "Live chat"}</strong>
            <button className="icon-button" type="button" aria-label="Close live chat" onClick={() => setOpen(false)}>Close</button>
          </div>
          <div className="chat-body" ref={bodyRef} aria-live="polite">
            {content}
            {error && <div className="bubble info">{error}</div>}
          </div>
          {user && activeThread && (
            <form className="chat-form" onSubmit={send}>
              <input value={text} onChange={(e) => setText(e.target.value)} maxLength={1000} autoComplete="off" aria-label="Type your message" placeholder="Type your message" />
              <button type="submit">Send</button>
            </form>
          )}
        </section>
      )}
      <button className="chat-toggle" type="button" aria-label={open ? "Close live chat" : "Open live chat"} aria-expanded={open} onClick={() => setOpen(!open)}>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" aria-hidden="true"><path d="M4 5h16v12H9l-5 4z" /></svg>
        {unread && <span className="chat-dot" />}
      </button>
    </>
  );
}
