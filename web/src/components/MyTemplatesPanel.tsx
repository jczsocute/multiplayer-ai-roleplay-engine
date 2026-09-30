import { useMemo, useRef, useState } from "react";
import {
  copyConfirmation, deleteConfirmation, formatLocalTime, newTemplatePayload,
  renamePrompt, sortedByUpdated, templateMeta, toggledRow,
  visibilityToggleLabel,
} from "../lobby";
import type { TemplateItem } from "../types";
import { downloadAdvancedTemplateZip } from "../templateZip";

type Props = {
  templates: TemplateItem[];
  roleCounts: number[];
  busy: boolean;
  error: string;
  onUse: (template: TemplateItem) => void;
  onDetail: (template: TemplateItem) => void;
  onEdit: (template: TemplateItem) => void;
  onImportZip: (file: File) => void;
  onExportZip: (template: TemplateItem) => void;
  onRename: (template: TemplateItem, name: string) => void;
  onToggleVisibility: (template: TemplateItem, isPublic: boolean) => void;
  onCopy: (template: TemplateItem) => void;
  onDelete: (template: TemplateItem) => void;
  onCreate: (name: string, roleCount: number) => void;
};

/** 我的剧本: create + per-row management. */
export function MyTemplatesPanel({
  templates, roleCounts, busy, error,
  onUse, onDetail, onEdit, onImportZip, onExportZip,
  onRename, onCopy, onDelete, onCreate, onToggleVisibility,
}: Props) {
  const uploadInput = useRef<HTMLInputElement>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [roleCount, setRoleCount] = useState(roleCounts[0] ?? 2);
  const [advancedBusy, setAdvancedBusy] = useState(false);
  const [advancedError, setAdvancedError] = useState("");
  const sorted = useMemo(() => sortedByUpdated(templates), [templates]);

  const rename = (template: TemplateItem) => {
    const value = window.prompt(renamePrompt("剧本", template.name), template.name);
    if (value !== null && value.trim() && value.trim() !== template.name) {
      onRename(template, value.trim());
    }
  };

  return <div className="resource-list">
    <div className="panel-actions">
      <button className="secondary compact-button" onClick={() => setCreating(!creating)}>
        {creating ? "收起" : "新建"}
      </button>
      <button className="secondary compact-button" disabled={busy}
        onClick={() => uploadInput.current?.click()}>上传zip新建</button>
      <button className="secondary compact-button" disabled={busy || advancedBusy}
        onClick={() => {
          setAdvancedBusy(true);
          setAdvancedError("");
          void downloadAdvancedTemplateZip().then((failure) => {
            if (failure) setAdvancedError(failure);
          }).finally(() => setAdvancedBusy(false));
        }}>下载高级剧本</button>
      <input ref={uploadInput} type="file" accept=".zip,application/zip" hidden
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) onImportZip(file);
          event.target.value = "";
        }} />
    </div>

    {creating && <form className="panel-form template-create-form" onSubmit={(event) => {
      event.preventDefault();
      const payload = newTemplatePayload(name, roleCount);
      if (!payload.name) return;
      onCreate(payload.name, payload.role_count);
      setName("");
      setCreating(false);
    }}>
      <input placeholder="剧本名称" value={name} maxLength={60}
        onChange={(event) => setName(event.target.value)} />
      <label className="radio-row">角色数量
        <select value={roleCount} onChange={(event) => setRoleCount(Number(event.target.value))}>
          {roleCounts.map((count) => <option key={count} value={count}>{count}</option>)}
        </select>
      </label>
      <p className="muted">新剧本使用项目自带的基础剧本生成角色，默认私有。</p>
      <div className="panel-actions">
        <button disabled={!name.trim() || busy}>{busy ? "创建中…" : "创建剧本"}</button>
      </div>
    </form>}

    {error && <p className="error-text">{error}</p>}
    {advancedError && <p className="error-text" role="alert">{advancedError}</p>}
    {!sorted.length && <p className="muted">还没有自己的剧本。</p>}

    {sorted.map((template) => <article className="row-card" key={template.id}>
      <button className="row-main row-toggle" aria-expanded={openId === template.id}
        onClick={() => setOpenId(toggledRow(openId, template.id))}>
        <strong>{template.name}</strong>
        <span className="muted">
          {formatLocalTime(template.updated_at)} · {templateMeta(template)}
        </span>
      </button>
      {openId === template.id && <div className="row-actions">
        <button onClick={() => onUse(template)}>使用</button>
        <button className="secondary" onClick={() => onDetail(template)}>详情</button>
        <button className="secondary" onClick={() => onEdit(template)}>
          编辑
        </button>
        <button className="secondary" onClick={() => onExportZip(template)}>导出zip</button>
        <button className="secondary" disabled={busy}
          onClick={() => onToggleVisibility(template, !template.is_public)}>
          {visibilityToggleLabel(template)}
        </button>
        <button className="secondary" onClick={() => rename(template)}>重命名</button>
        <button className="secondary" onClick={() => {
          if (window.confirm(copyConfirmation("剧本", template.name))) onCopy(template);
        }}>复制</button>
        <button className="danger" onClick={() => {
          if (window.confirm(deleteConfirmation("剧本", template.name, "已用它创建的存档不受影响。"))) {
            onDelete(template);
          }
        }}>删除</button>
      </div>}
    </article>)}
  </div>;
}
