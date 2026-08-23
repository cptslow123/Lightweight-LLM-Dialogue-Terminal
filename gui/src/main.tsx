import { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  ChevronDown,
  Copy,
  Globe2,
  PanelLeft,
  PanelLeftClose,
  Paperclip,
  Plus,
  Send,
  Settings,
  Square,
  Trash2,
} from "lucide-react";
import "./styles.css";

type Conversation = {
  id: number;
  title: string;
  model: string;
  thinking: string;
};
type Message = { id?: number; role: string; content: string };
type Attachment = { id: string; name: string; kind: "image" | "text" | "binary"; size: number; preview?: string; data_url?: string; text?: string; base64?: string };
type Config = {
  defaults: Record<string, string | number | boolean>;
  providers: ProviderConfig[];
  paths?: { config: string; database: string };
};
type ProviderConfig = { name: string; base_url: string; api_key_env?: string; api_key?: string; api_key_configured?: boolean; models: string[] };
// Vite supplies this at build time. URL inference is unreliable for packaged
// Tauri WebViews because they may also use an HTTP-like origin.
const apiBase = import.meta.env.DEV ? "" : "http://127.0.0.1:18765";
const request = (path: string, init?: RequestInit) =>
  fetch(apiBase + path, {
    ...init,
    headers: {
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...(init?.headers || {}),
    },
  });

async function readJson(response: Response) {
  const body = await response.text();
  if (!response.ok)
    throw new Error(body || "本地服务返回 HTTP " + response.status);
  if (!body.trim()) throw new Error("本地服务返回了空响应");
  try {
    return JSON.parse(body);
  } catch {
    throw new Error("本地服务返回了无效数据：" + body.slice(0, 120));
  }
}

function readFile(file: File, mode: "dataUrl" | "base64"): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("读取附件失败：" + file.name));
    reader.onload = () => {
      const result = String(reader.result || "");
      resolve(mode === "base64" ? result.split(",", 2)[1] || "" : result);
    };
    if (mode === "base64") reader.readAsDataURL(file);
    else reader.readAsDataURL(file);
  });
}

function formatElapsed(milliseconds: number) {
  const seconds = Math.floor(milliseconds / 1000);
  return seconds < 60 ? seconds + " 秒" : Math.floor(seconds / 60) + " 分 " + seconds % 60 + " 秒";
}

async function loadConfigWithRetry() {
  let lastError: unknown;
  for (let attempt = 0; attempt < 100; attempt += 1) {
    try {
      const health = await readJson(await request("/api/health"));
      if (health.ok !== true || health.app !== "light-harness-gui")
        throw new Error("本地服务不是 Light Harness GUI 后端");
      return (await readJson(await request("/api/config"))) as Config;
    } catch (cause) {
      lastError = cause;
      await new Promise((resolve) => window.setTimeout(resolve, 250));
    }
  }
  throw lastError || new Error("本地服务未能在启动时就绪");
}

function App() {
  const [config, setConfig] = useState<Config>({ defaults: {}, providers: [] });
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<number>();
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [provider, setProvider] = useState("");
  const [model, setModel] = useState("");
  const [thinking, setThinking] = useState("off");
  const [search, setSearch] = useState(true);
  const [streaming, setStreaming] = useState(false);
  const [sidebar, setSidebar] = useState(true);
  const [settings, setSettings] = useState(false);
  const [settingsTab, setSettingsTab] = useState<"general" | "search" | "providers">("general");
  const [editingProvider, setEditingProvider] = useState(0);
  const [draftProviders, setDraftProviders] = useState<ProviderConfig[]>([]);
  const [draftDefaults, setDraftDefaults] = useState<Record<string, string | number | boolean>>({});
  const [error, setError] = useState("");
  const [searching, setSearching] = useState("");
  const [reasoning, setReasoning] = useState("");
  const [elapsed, setElapsed] = useState(0);
  const [reasoningStartedAt, setReasoningStartedAt] = useState<number>();
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const endRef = useRef<HTMLDivElement>(null);
  const cancelRef = useRef<AbortController>();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const attachmentCounter = useRef(0);
  const generationStartedAt = useRef(0);
  useEffect(() => {
    if (!streaming) return;
    const timer = window.setInterval(() => setElapsed(Date.now() - generationStartedAt.current), 250);
    return () => window.clearInterval(timer);
  }, [streaming]);
  const addFiles = async (files: File[]) => {
    const valid = files.filter((file) => file.size <= 20 * 1024 * 1024);
    const rejected = files.filter((file) => file.size > 20 * 1024 * 1024);
    if (rejected.length) setError("以下文件超过 20 MB：" + rejected.map((file) => file.name).join("、"));
    const next = await Promise.all(valid.map(async (file): Promise<Attachment> => {
      const id = String(++attachmentCounter.current);
      if (file.type.startsWith("image/")) {
        return { id, name: file.name || "粘贴图片.png", kind: "image", size: file.size, preview: URL.createObjectURL(file), data_url: await readFile(file, "dataUrl") };
      }
      const extension = file.name.split(".").pop()?.toLowerCase() || "";
      if (["pdf", "docx", "xlsx"].includes(extension)) {
        return { id, name: file.name, kind: "binary", size: file.size, base64: await readFile(file, "base64") };
      }
      return { id, name: file.name, kind: "text", size: file.size, text: await file.text() };
    }));
    setAttachments((old) => old.concat(next));
  };
  const removeAttachment = (id: string) => setAttachments((old) => {
    const attachment = old.find((item) => item.id === id);
    if (attachment?.preview) URL.revokeObjectURL(attachment.preview);
    return old.filter((item) => item.id !== id);
  });
  const selectModel = (value: string) => {
    const [nextProvider, ...modelParts] = value.split("::");
    setProvider(nextProvider);
    setModel(modelParts.join("::"));
  };
  const applyConfig = (next: Config) => {
    setConfig(next);
    setDraftProviders(next.providers.map((item) => ({ ...item, models: [...(item.models || [])] })));
    setDraftDefaults({ ...next.defaults });
  };
  const openSettings = () => {
    setError("");
    applyConfig(config);
    setSettingsTab("general");
    setEditingProvider(0);
    setSettings(true);
    void (async () => {
      try {
        applyConfig(await loadConfigWithRetry());
      } catch (cause) {
        setError((cause as Error).message || "无法读取本地配置");
      }
    })();
  };
  const updateProvider = (index: number, patch: Partial<ProviderConfig>) => setDraftProviders((items) => items.map((item, itemIndex) => itemIndex === index ? { ...item, ...patch } : item));
  const removeProvider = (index: number) => {
    setDraftProviders((items) => items.filter((_, itemIndex) => itemIndex !== index));
    setEditingProvider((current) => Math.max(0, current > index ? current - 1 : current));
  };

  async function refreshConversations() {
    const response = await request("/api/conversations");
    const data = await readJson(response);
    const items = data.conversations || [];
    setConversations(items);
    return items as Conversation[];
  }
  async function loadMessages(id: number) {
    const response = await request("/api/conversations/" + id + "/messages");
    const data = await readJson(response);
    setMessages(
      (data.messages || []).filter((m: Message) => m.role !== "system"),
    );
  }
  useEffect(() => {
    void (async () => {
      try {
        const next = await loadConfigWithRetry();
        applyConfig(next);
        setProvider(next.defaults.provider || next.providers.find((item) => item.models?.includes(next.defaults.model))?.name || next.providers[0]?.name || "");
        setModel(next.defaults.model || next.providers[0]?.models?.[0] || "");
        setThinking(next.defaults.thinking || "off");
        const items = await refreshConversations();
        if (items[0]) setActiveId(items[0].id);
      } catch (cause) {
        setError(
          (cause as Error).message ||
            "本地服务尚未启动，请重新打开 Light Harness。",
        );
      }
    })();
  }, []);
  useEffect(() => {
    if (activeId) void loadMessages(activeId);
  }, [activeId]);
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, reasoning, searching]);
  async function newChat() {
    const r = await request("/api/conversations", {
      method: "POST",
      body: JSON.stringify({ title: "新对话", provider, model, thinking }),
    });
    const c = await readJson(r);
    setActiveId(c.id);
    setMessages([]);
    await refreshConversations();
  }
  async function removeChat(id: number) {
    await request("/api/conversations/" + id, { method: "DELETE" });
    const items = await refreshConversations();
    setActiveId(items[0]?.id);
  }
  async function send() {
    const content = input.trim();
    if ((!content && !attachments.length) || streaming) return;
    const outgoingAttachments = attachments;
    setInput("");
    setAttachments([]);
    setStreaming(true);
    setError("");
    setSearching("");
    setReasoning("");
    setReasoningStartedAt(undefined);
    generationStartedAt.current = Date.now();
    setElapsed(0);
    setMessages((old) =>
      old.concat([
        { role: "user", content: [content, ...outgoingAttachments.map((item) => "[" + (item.kind === "image" ? "图片" : "文件") + "：" + item.name + "]")].filter(Boolean).join("\n") },
        { role: "assistant", content: "" },
      ]),
    );
    const controller = new AbortController();
    cancelRef.current = controller;
    try {
      const response = await request("/api/chat", {
        method: "POST",
        signal: controller.signal,
        body: JSON.stringify({
          conversation_id: activeId,
          message: content,
          provider,
          model,
          thinking,
          search,
          attachments: outgoingAttachments.map(({ id: _id, preview: _preview, size: _size, ...item }) => item),
        }),
      });
      if (!response.ok || !response.body)
        throw new Error((await response.text()) || "模型请求失败");
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let pending = "";
      while (true) {
        const part = await reader.read();
        if (part.done) break;
        pending += decoder.decode(part.value, { stream: true });
        const lines = pending.split("\n");
        pending = lines.pop() || "";
        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const event = JSON.parse(line.slice(6));
          if (event.type === "delta")
            setMessages((old) => {
              const copy = old.slice();
              const last = copy.length - 1;
              copy[last] = {
                ...copy[last],
                content: copy[last].content + event.text,
              };
              return copy;
            });
          if (event.type === "reasoning") {
            setReasoning(event.text);
            setReasoningStartedAt((startedAt) => startedAt || Date.now());
          }
          if (event.type === "search") setSearching(event.query);
          if (event.type === "error") setError(event.message || "请求失败");
          if (event.type === "done" && event.conversation_id !== activeId)
            setActiveId(event.conversation_id);
        }
      }
      await refreshConversations();
    } catch (cause) {
      if ((cause as Error).name !== "AbortError")
        setError((cause as Error).message || "发送失败");
    } finally {
      outgoingAttachments.forEach((item) => item.preview && URL.revokeObjectURL(item.preview));
      setStreaming(false);
      cancelRef.current = undefined;
    }
  }
  async function saveSettings() {
    const response = await request("/api/settings", {
      method: "PATCH",
      body: JSON.stringify({
        providers: draftProviders.map((item) => ({ ...item, models: typeof item.models === "string" ? item.models.split(",").map((model) => model.trim()) : item.models })),
        defaults: { ...draftDefaults, provider, model, thinking },
      }),
    });
    const next = await readJson(response) as Config;
    applyConfig(next);
    setSettings(false);
  }
  return (
    <div className="app-shell">
      <header className="topbar">
        <button
          className="icon-button"
          title="切换会话侧栏"
          onClick={() => setSidebar(!sidebar)}
        >
          {sidebar ? <PanelLeftClose /> : <PanelLeft />}
        </button>
        <div className="brand">
          <span>✦</span> Light Harness
        </div>
        <label className="active-model">
          <select
            value={provider + "::" + model}
            onChange={(e) => selectModel(e.target.value)}
            title="切换模型"
          >
            {config.providers
              .filter((item) => item.models?.length)
              .map((item) => (
                <optgroup label={item.name} key={item.name}>
                  {item.models.map((name) => (
                    <option value={item.name + "::" + name} key={item.name + "::" + name}>
                      {item.name} · {name}
                    </option>
                  ))}
                </optgroup>
              ))}
          </select>
          <ChevronDown size={15} />
        </label>
        <div className="grow" />
        <button
          className="icon-button"
          title="设置"
          onClick={openSettings}
        >
          <Settings />
        </button>
      </header>
      <div className="workspace">
        {sidebar && (
          <aside className="sidebar">
            <button className="new-chat" onClick={() => void newChat()}>
              <Plus size={17} /> 新对话
            </button>
            <span className="sidebar-label">最近对话</span>
            <nav>
              {conversations.map((c) => (
                <div
                  className={
                    "conversation " + (c.id === activeId ? "selected" : "")
                  }
                  key={c.id}
                >
                  <button onClick={() => setActiveId(c.id)}>
                    {c.title || "未命名对话"}
                  </button>
                  <button
                    className="delete-chat"
                    title="删除会话"
                    onClick={() => void removeChat(c.id)}
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              ))}
            </nav>
            <button
              className="sidebar-settings"
              onClick={openSettings}
            >
              <Settings size={16} /> 设置
            </button>
          </aside>
        )}
        <main className="chat-area">
          <div className="messages">
            {!messages.length && (
              <section className="welcome">
                <div className="welcome-symbol">✦</div>
                <h1>今天想聊点什么？</h1>
                <p>连接你的模型，开始一段新的思考。</p>
                <div className="prompts">
                  <button onClick={() => setInput("帮我总结这段文字")}>
                    总结一段文字
                  </button>
                  <button onClick={() => setInput("帮我制定一个计划")}>
                    制定一个计划
                  </button>
                  <button onClick={() => setInput("解释一个复杂概念")}>
                    解释复杂概念
                  </button>
                </div>
              </section>
            )}
            {messages.map((m, index) => (
              <article
                className={"message " + m.role}
                key={(m.id || "live") + "-" + index}
              >
                <div className="avatar">{m.role === "user" ? "你" : "✦"}</div>
                <div className="message-body">
                  {m.role === "assistant" &&
                    reasoning &&
                    index === messages.length - 1 && (
                      <details className="reasoning">
                        <summary>思考过程{streaming && reasoningStartedAt ? " · " + formatElapsed(Date.now() - reasoningStartedAt) : ""}</summary>
                        <pre>{reasoning}</pre>
                      </details>
                    )}
                  {m.role === "assistant" ? (
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>
                      {m.content || "▍"}
                    </ReactMarkdown>
                  ) : (
                    <p>{m.content}</p>
                  )}
                </div>
                {m.role === "assistant" && m.content && (
                  <button
                    title="复制回答"
                    className="copy-answer"
                    onClick={() => navigator.clipboard.writeText(m.content)}
                  >
                    <Copy size={14} />
                  </button>
                )}
              </article>
            ))}
            {searching && (
              <div className="search-status">
                <Globe2 size={15} /> 正在通过 Firecrawl 搜索：{searching}
              </div>
            )}
            {streaming && (
              <div className="generation-status">正在生成 · {formatElapsed(elapsed)}</div>
            )}
            <div ref={endRef} />
          </div>
          <div className="composer-container">
            <div
              className="composer"
              onDragOver={(event) => event.preventDefault()}
              onDrop={(event) => {
                event.preventDefault();
                void addFiles(Array.from(event.dataTransfer.files));
              }}
            >
              <input
                ref={fileInputRef}
                className="attachment-input"
                type="file"
                multiple
                onChange={(event) => {
                  void addFiles(Array.from(event.target.files || []));
                  event.target.value = "";
                }}
              />
              {attachments.length > 0 && (
                <div className="attachment-list">
                  {attachments.map((item) => (
                    <div className="attachment-chip" key={item.id}>
                      {item.preview ? <img src={item.preview} alt="" /> : <Paperclip size={14} />}
                      <span title={item.name}>{item.name}</span>
                      <button title="移除附件" onClick={() => removeAttachment(item.id)}>×</button>
                    </div>
                  ))}
                </div>
              )}
              <textarea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onPaste={(event) => {
                  const files = Array.from(event.clipboardData.files);
                  if (files.length) {
                    event.preventDefault();
                    void addFiles(files);
                  }
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    void send();
                  }
                }}
                placeholder="输入消息..."
                rows={2}
              />
              <div className="composer-controls">
                <button className="icon-button" title="添加附件" onClick={() => fileInputRef.current?.click()}>
                  <Paperclip size={17} />
                </button>
                <button
                  className={"search-toggle " + (search ? "on" : "")}
                  onClick={() => setSearch(!search)}
                >
                  <Globe2 size={15} /> 联网
                </button>
                <select
                  value={thinking}
                  onChange={(e) => setThinking(e.target.value)}
                >
                  <option value="off">思考关闭</option>
                  <option value="low">思考 · 低</option>
                  <option value="medium">思考 · 中</option>
                  <option value="high">思考 · 高</option>
                </select>
                <div className="grow" />
                {streaming ? (
                  <button
                    title="停止生成"
                    className="send-button stop"
                    onClick={() => cancelRef.current?.abort()}
                  >
                    <Square size={14} fill="currentColor" />
                  </button>
                ) : (
                  <button
                    title="发送"
                    className="send-button"
                    disabled={!input.trim() && !attachments.length}
                    onClick={() => void send()}
                  >
                    <Send size={17} />
                  </button>
                )}
              </div>
            </div>
            <p className="disclaimer">AI 可能会出错，请核实重要信息</p>
          </div>
        </main>
        {settings && (
          <section className="settings-panel">
            <div className="settings-title">
              <strong>设置</strong>
              <button
                className="icon-button"
                onClick={() => setSettings(false)}
              >
                ×
              </button>
            </div>
            <div className="settings-tabs"><button className={settingsTab === "general" ? "selected" : ""} onClick={() => setSettingsTab("general")}>常规</button><button className={settingsTab === "search" ? "selected" : ""} onClick={() => setSettingsTab("search")}>搜索</button><button className={settingsTab === "providers" ? "selected" : ""} onClick={() => setSettingsTab("providers")}>模型服务</button></div>
            <div className="settings-scroll">
              {settingsTab === "general" && <><h2>默认对话</h2><label>默认思考档位<select value={thinking} onChange={(e) => { setThinking(e.target.value); setDraftDefaults((old) => ({ ...old, thinking: e.target.value })); }}><option value="off">关闭</option><option value="low">低</option><option value="medium">中</option><option value="high">高</option></select></label><label>上下文窗口<input type="number" value={String(draftDefaults.context_window || "")} onChange={(e) => setDraftDefaults((old) => ({ ...old, context_window: Number(e.target.value) || "" }))} /></label><label>最大输入 tokens<input type="number" value={String(draftDefaults.max_input || "")} onChange={(e) => setDraftDefaults((old) => ({ ...old, max_input: Number(e.target.value) || "" }))} /></label><label>最大输出 tokens<input type="number" value={String(draftDefaults.max_tokens || "")} onChange={(e) => setDraftDefaults((old) => ({ ...old, max_tokens: Number(e.target.value) || "" }))} /></label></>}
              {settingsTab === "search" && <><h2>联网搜索</h2><label>搜索服务<select value={String(draftDefaults.search_provider || "firecrawl")} onChange={(e) => setDraftDefaults((old) => ({ ...old, search_provider: e.target.value }))}><option value="firecrawl">Firecrawl</option><option value="tavily">Tavily</option></select></label><label>Firecrawl API key<input placeholder="fc-..." value={String(draftDefaults.firecrawl_api_key || "")} onChange={(e) => setDraftDefaults((old) => ({ ...old, firecrawl_api_key: e.target.value }))} /></label><label>Tavily API key<input placeholder="tvly-..." value={String(draftDefaults.tavily_api_key || "")} onChange={(e) => setDraftDefaults((old) => ({ ...old, tavily_api_key: e.target.value }))} /></label></>}
              {settingsTab === "providers" && <div className="provider-settings"><div className="provider-list">{draftProviders.map((item, index) => <button className={index === editingProvider ? "selected" : ""} key={index} onClick={() => setEditingProvider(index)}><span>{item.name || "新服务"}</span><small>{item.api_key_configured ? "Key 已配置" : "未配置 Key"}</small></button>)}<button className="add-provider" onClick={() => { setDraftProviders((items) => items.concat({ name: "", base_url: "", api_key_env: "", api_key: "", models: [] })); setEditingProvider(draftProviders.length); }}>+ 添加服务</button></div>{draftProviders[editingProvider] && <div className="provider-card"><div className="provider-card-title"><strong>{draftProviders[editingProvider].name || "新服务"}</strong><button title="删除服务" className="remove-provider" onClick={() => removeProvider(editingProvider)}>删除</button></div><label>名称<input value={draftProviders[editingProvider].name} onChange={(e) => updateProvider(editingProvider, { name: e.target.value })} placeholder="例如 deepseek" /></label><label>Base URL<input value={draftProviders[editingProvider].base_url || ""} onChange={(e) => updateProvider(editingProvider, { base_url: e.target.value })} placeholder="https://api.example.com/v1" /></label><label>API key<input value={draftProviders[editingProvider].api_key || ""} placeholder="sk-..." onChange={(e) => updateProvider(editingProvider, { api_key: e.target.value })} /></label><label>API key 环境变量<input value={draftProviders[editingProvider].api_key_env || ""} onChange={(e) => updateProvider(editingProvider, { api_key_env: e.target.value })} placeholder="可选，例如 DEEPSEEK_API_KEY" /></label><label>模型列表<input value={(draftProviders[editingProvider].models || []).join(", ")} onChange={(e) => updateProvider(editingProvider, { models: e.target.value.split(",").map((model) => model.trim()).filter(Boolean) })} placeholder="模型名使用英文逗号分隔" /></label></div>}</div>}
            </div>
            <div className="config-location">
              配置：{config.paths?.config || "data/config.toml"}
              <br />
              数据库：{config.paths?.database || "data/chat.db"}
            </div>
            <button
              className="save-settings"
              onClick={() => void saveSettings()}
            >
              保存设置
            </button>
          </section>
        )}
      </div>
      {error && (
        <div className="error-toast">
          <span>{error}</span>
          <button className="close-error" title="关闭错误提示" onClick={() => setError("")}>
            ×
          </button>
        </div>
      )}
    </div>
  );
}
createRoot(document.getElementById("root")!).render(<App />);
