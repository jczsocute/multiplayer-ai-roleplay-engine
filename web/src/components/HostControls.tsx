import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

import {
  closeRoomDisabled,
  defaultRollbackRound,
  hostControlsDisabled,
  parseRollbackRound,
  retryConfirmation,
  retryMessage,
  rollbackConfirmation,
  rollbackMessage,
  rollbackRoundInput,
} from "../host";
import type { ClientMessage, RoleDefinition } from "../protocol";
import { translateUi, uiConfirm, useLanguage } from "../i18n";

export type RoomMember = {
  user_id?: number;
  name: string;
  role?: string | null;
  assigned_roles?: string[];
  connected?: boolean;
};

export type HostControlsProps = {
  connection: string;
  processingStage: string | null;
  round: number | null;
  ownerUserId?: number;
  send: (message: ClientMessage) => void;
  roles: RoleDefinition[];
  users: RoomMember[];
  closeRoom?: () => void;
  exportHistory?: () => Promise<string | null>;
};

/** The dialog body: no portal and no toggle button, so it renders anywhere. */
export function HostControlsDialog({
  connection, processingStage, round, ownerUserId, roles, users, send, exportHistory, onClose,
}: HostControlsProps & { onClose: () => void }) {
  const { language } = useLanguage();
  const [confirming, setConfirming] = useState<"retry" | "rollback" | null>(null);
  const [target, setTarget] = useState(() => rollbackRoundInput(round));
  const [assignments, setAssignments] = useState<Record<string, number>>({});
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState("");

  const disabled = hostControlsDisabled({ connection, processingStage });
  const processingLocked = processingStage !== null;
  const canKick = connection === "CONNECTED";
  // Only connected members can take a role; everyone with a seat is listed.
  const assignable = users.filter((user) => user.connected !== false);
  const fallbackRound = defaultRollbackRound(round);
  const rollbackRound = parseRollbackRound(target);

  // The target round starts as the previous round; the owner can overwrite it.
  useEffect(() => {
    setTarget(rollbackRoundInput(round));
  }, [round]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      className="host-overlay"
      role="dialog"
      aria-modal="true"
      aria-label="房主管理"
      onClick={(event) => { if (event.target === event.currentTarget) onClose(); }}
    >
      <section className="host-controls">
        <div className="host-controls-head">
          <strong>房主管理</strong>
          <span className="muted">{language === "en" ? `Current round ${round ?? "—"}` : `当前第 ${round ?? "—"} 回合`}</span>
          <button className="secondary compact-button host-close" onClick={onClose}>✕ 关闭</button>
        </div>

        <section className="host-section">
          <h3>重新生成</h3>
          {confirming === "retry" ? (
            <div className="host-confirm">
              <p className="muted">{language === "en" ? "Regenerate the last round using the same actions. Current next-round drafts will be cleared." : retryConfirmation()}</p>
              <div className="host-confirm-actions">
                <button
                  disabled={disabled}
                  onClick={() => { setConfirming(null); send(retryMessage()); }}
                >
                  确认重新生成
                </button>
                <button className="secondary" onClick={() => setConfirming(null)}>取消</button>
              </div>
            </div>
          ) : (
            <div className="host-row">
              <button className="secondary" disabled={disabled} onClick={() => setConfirming("retry")}>
                重新生成上一回合
              </button>
            </div>
          )}
        </section>

        <section className="host-section">
          <h3>房间成员</h3>
          <ul className="member-list">
            {users.map((member) => <li key={member.user_id} className="member-row">
              <span className="member-name">
                {member.name}
                {member.user_id === ownerUserId && <span className="host-badge">房主</span>}
                {member.connected === false && <span className="muted"> · 离线中</span>}
                {(member.assigned_roles?.length || member.role) && <span className="muted"> · {
                  (member.assigned_roles?.length ? member.assigned_roles : [member.role])
                    .map((role) => roles.find((item) => item.id === role)?.name ?? role).join("、")
                }</span>}
              </span>
              {member.user_id !== ownerUserId && canKick && <button className="secondary member-kick"
                onClick={() => send({ type: "kick_user", user_id: Number(member.user_id) })}>
                踢出
              </button>}
            </li>)}
          </ul>
        </section>

        <section className="host-section">
          <h3>角色分配</h3>
          <div className="host-assignments">
            {roles.map((role) => <label key={role.id}>{role.name}
              <select value={assignments[role.id] ?? ""} onChange={(event) =>
                setAssignments({ ...assignments, [role.id]: Number(event.target.value) })}>
                <option value="">选择用户</option>
                {assignable.map((user) => <option key={user.user_id} value={user.user_id}>
                  {user.name}
                </option>)}
              </select>
            </label>)}
          </div>
          <div className="host-row">
            <button disabled={disabled || processingLocked || roles.some((role) => !assignments[role.id])}
              onClick={() => send({ type: "assign_roles", assignments })}>
              应用角色分配
            </button>
          </div>
        </section>

        <section className="host-section">
          <h3>回滚剧情</h3>
          {confirming === "rollback" && rollbackRound !== null ? (
            <div className="host-confirm">
              <p className="muted">{language === "en" ? `Roll back to Round ${rollbackRound}? Later rounds and actions will be deleted.` : rollbackConfirmation(rollbackRound)}</p>
              <div className="host-confirm-actions">
                <button
                  disabled={disabled}
                  onClick={() => {
                    setConfirming(null);
                    send(rollbackMessage(rollbackRound));
                  }}
                >
                  确认执行回滚
                </button>
                <button className="secondary" onClick={() => setConfirming(null)}>取消</button>
              </div>
            </div>
          ) : (
            <div className="host-row host-rollback">
              <label className="muted" htmlFor="host-rollback-round">目标回合</label>
              <input
                id="host-rollback-round"
                inputMode="numeric"
                value={target}
                placeholder={fallbackRound === null ? "—" : String(fallbackRound)}
                onChange={(event) => setTarget(event.target.value)}
              />
              <button
                className="secondary"
                disabled={disabled || rollbackRound === null}
                onClick={() => setConfirming("rollback")}
              >
                执行回滚
              </button>
            </div>
          )}
        </section>

        {exportHistory && <section className="host-section">
          <h3>历史记录</h3>
          <div className="host-row">
            <button className="secondary" disabled={disabled || processingLocked || exporting}
              onClick={async () => {
                setExporting(true); setExportError("");
                try {
                  const error = await exportHistory();
                  if (error) setExportError(error);
                } finally { setExporting(false); }
              }}>
              {exporting ? "导出中…" : "导出历史记录"}
            </button>
          </div>
          {processingLocked && <p className="muted">{processingStage === "FAILED" ? "本轮生成失败，请重新生成后导出。" : "请等待本轮完成……"}</p>}
          {exportError && <p className="error-text" role="alert">{exportError}</p>}
        </section>}

      </section>
    </div>
  );
}

/** Header entry point: 房主管理 and 关闭房间 buttons, plus the floating dialog.
 *
 * For a host, closing the Room replaces 离开房间, so the button lives in the top
 * row next to 房主管理 and the dialog itself only manages the Room.
 */
export function HostControls(props: HostControlsProps) {
  const [open, setOpen] = useState(false);
  const close = () => setOpen(false);

  const toggle = (
    <button
      className={`secondary header-button${open ? " active" : ""}`}
      aria-haspopup="dialog"
      aria-expanded={open}
      onClick={() => setOpen(!open)}
    >
      ⚙ 房主管理
    </button>
  );
  const closeRoomButton = props.closeRoom && (
    <button className="danger header-button" disabled={closeRoomDisabled(props)} onClick={() => {
      if (uiConfirm("关闭房间会断开所有用户，但不会删除游戏存档。确定关闭？")) {
        props.closeRoom?.();
      }
    }}>关闭房间</button>
  );

  return <>
    {toggle}
    {closeRoomButton}
    {open && createPortal(
      <HostControlsDialog {...props} onClose={close} />, document.body,
    )}
  </>;
}
