import { useEffect, useRef, useState } from "react";
import { apiGet, apiPut } from "../api";
import type { TemplateEditorData } from "../types";
import { downloadTemplateZip, uploadTemplateZip } from "../templateZip";

type Props = {
  templateId: string;
  roleCounts: number[];
  onBack: () => void;
  onSaved: () => Promise<void>;
};

export function resizeCharacters(
  characters: TemplateEditorData["characters"], count: number,
): TemplateEditorData["characters"] {
  const next = characters.slice(0, count);
  for (let index = next.length + 1; index <= count; index += 1) {
    next.push({ index, character: "", opening: "" });
  }
  return next;
}

export function parseEditorTags(text: string): string[] {
  return text.split(/[,，\n]/).map((tag) => tag.trim()).filter(Boolean);
}

export function TemplateEditor({ templateId, roleCounts, onBack, onSaved }: Props) {
  const uploadInput = useRef<HTMLInputElement>(null);
  const [saved, setSaved] = useState<TemplateEditorData | null>(null);
  const [draft, setDraft] = useState<TemplateEditorData | null>(null);
  const [tagsText, setTagsText] = useState("");
  const [savedTagsText, setSavedTagsText] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [zipBusy, setZipBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const dirty = Boolean(saved && draft &&
    (JSON.stringify(draft) !== JSON.stringify(saved) || tagsText !== savedTagsText));
  const working = saving || zipBusy;

  useEffect(() => {
    let active = true;
    void apiGet<TemplateEditorData>(`/api/templates/${encodeURIComponent(templateId)}/editor`)
      .then((result) => {
        if (!active) return;
        if (result.ok) {
          setSaved(result.data);
          setDraft(result.data);
          const tags = result.data.tags.join("，");
          setTagsText(tags);
          setSavedTagsText(tags);
        } else setError(result.error);
      }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [templateId]);

  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const update = (patch: Partial<TemplateEditorData>) => {
    setDraft((current) => current ? { ...current, ...patch } : current);
    setNotice("");
  };
  const changeCount = (count: number) => {
    if (!draft || !roleCounts.includes(count)) return;
    update({ characters: resizeCharacters(draft.characters, count) });
  };
  const changeCharacter = (index: number, field: "character" | "opening", value: string) => {
    if (!draft) return;
    update({ characters: draft.characters.map((item) =>
      item.index === index ? { ...item, [field]: value } : item) });
  };
  const back = () => {
    if (!dirty || window.confirm("有未保存的修改，确定离开吗？")) onBack();
  };
  const importZip = async (file: File) => {
    if (working) return;
    const prompt = dirty
      ? "导入 ZIP 将覆盖当前剧本，未保存的修改会丢失。确定继续吗？"
      : "导入 ZIP 会覆盖当前剧本的全部内容。确定继续吗？";
    if (!window.confirm(prompt)) return;
    setZipBusy(true);
    setError(""); setNotice("");
    const result = await uploadTemplateZip(file, templateId);
    if (!result.ok) { setError(result.error); setZipBusy(false); return; }
    const loaded = await apiGet<TemplateEditorData>(`/api/templates/${encodeURIComponent(templateId)}/editor`);
    if (!loaded.ok) { setError(loaded.error); setZipBusy(false); return; }
    setSaved(loaded.data); setDraft(loaded.data);
    const tags = loaded.data.tags.join("，");
    setTagsText(tags); setSavedTagsText(tags);
    setNotice("ZIP 已导入，剧本内容已更新。");
    setZipBusy(false);
    await onSaved();
  };
  const downloadZip = async () => {
    if (working) return;
    if (dirty && !window.confirm("ZIP 只包含已保存的内容。继续下载吗？")) return;
    setZipBusy(true);
    setError("");
    const failure = await downloadTemplateZip(templateId);
    if (failure) setError(failure);
    setZipBusy(false);
  };
  const save = async () => {
    if (!draft || working) return;
    setSaving(true);
    setError("");
    setNotice("");
    const body = { ...draft, tags: parseEditorTags(tagsText) };
    const result = await apiPut<TemplateEditorData>(
      `/api/templates/${encodeURIComponent(templateId)}/editor`, body,
    );
    setSaving(false);
    if (!result.ok) { setError(result.error); return; }
    setSaved(result.data);
    setDraft(result.data);
    const tags = result.data.tags.join("，");
    setTagsText(tags);
    setSavedTagsText(tags);
    setNotice("剧本已保存。");
    await onSaved();
  };

  return <main className="lobby-shell template-editor-shell">
    <header className="lobby-header">
      <div><h1>编辑剧本</h1><p className="muted">填写文字内容后保存，即可用于创建存档。</p></div>
      <button className="secondary" onClick={back} disabled={working}>返回我的剧本</button>
    </header>
    {loading && <p>正在加载剧本…</p>}
    {error && <p className="error-text" role="alert">{error}</p>}
    {!loading && !draft && <button onClick={back}>返回</button>}
    {draft && <div className="template-editor-content">
      <fieldset className="editor-fieldset" disabled={working}>
      <section className="panel panel-form">
        <h2>基本信息</h2>
        <label>标题<input value={draft.title} maxLength={60}
          onChange={(event) => update({ title: event.target.value })} /></label>
        <label>简介<textarea rows={4} maxLength={1000} value={draft.introduction}
          onChange={(event) => update({ introduction: event.target.value })} /></label>
        <label>标签（用逗号分隔）<input value={tagsText}
          onChange={(event) => { setTagsText(event.target.value); setNotice(""); }} /></label>
      </section>
      <section className="panel panel-form">
        <h2>世界</h2>
        <label>世界设定<textarea className="editor-large-text" value={draft.world}
          onChange={(event) => update({ world: event.target.value })} /></label>
      </section>
      <section className="panel panel-form">
        <h2>角色</h2>
        <label>角色数量<select value={draft.characters.length}
          onChange={(event) => changeCount(Number(event.target.value))}>
          {roleCounts.map((count) => <option value={count} key={count}>{count}</option>)}
        </select></label>
        <p className="muted">新增角色会加入末尾；减少角色会删除末尾角色的内容。</p>
        {draft.characters.map((item) => <article className="editor-character-card" key={item.index}>
          <h3>角色 {item.index}</h3>
          <label>角色人设<textarea className="editor-large-text" value={item.character}
            onChange={(event) => changeCharacter(item.index, "character", event.target.value)} /></label>
          <label>开场白<textarea rows={5} value={item.opening}
            onChange={(event) => changeCharacter(item.index, "opening", event.target.value)} /></label>
        </article>)}
      </section>
      <section className="panel panel-form">
        <h2>AI 写作风格 / 创作要求</h2>
        <textarea rows={7} value={draft.ai_guidelines}
          onChange={(event) => update({ ai_guidelines: event.target.value })} />
      </section>
      <section className="panel panel-form">
        <h2>高级</h2>
        <ul className="muted editor-advanced-notes">
          <li>高级 Schema 与 Prompt 设置不会在此页面显示。</li>
          <li>暂时不支持角色状态栏在线编辑。</li>
          <li>如需编辑角色状态栏或更多 prompt，请下载 zip 项目，编辑完成后导入。</li>
        </ul>
        <div className="panel-actions">
          <button type="button" className="secondary" onClick={() => void downloadZip()}>下载zip</button>
          <button type="button" className="secondary" onClick={() => uploadInput.current?.click()}>导入zip</button>
          <input ref={uploadInput} type="file" accept=".zip,application/zip" hidden
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) void importZip(file);
              event.target.value = "";
            }} />
        </div>
      </section>
      </fieldset>
      <div className="panel-actions editor-actions">
        <button onClick={() => void save()} disabled={working || !dirty || !draft.title.trim()}>
          {saving ? "保存中…" : "保存"}
        </button>
        <button className="secondary" onClick={back} disabled={working}>取消 / 返回</button>
        {notice && <span role="status">{notice}</span>}
      </div>
    </div>}
  </main>;
}
