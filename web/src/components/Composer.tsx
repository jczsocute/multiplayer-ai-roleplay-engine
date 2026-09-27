import type { PlayerStatus } from "../protocol";
import { MAX_ACTION_LENGTH } from "../protocol";

type Props = {
  draft: string; status: PlayerStatus | undefined; onDraft: (value: string) => void;
  onSubmit: () => void; onCancel: () => void; onPause: () => void; onResume: () => void;
  disabled?: boolean;
};
export function Composer(props: Props) {
  const editable = props.status === "EDITING" && !props.disabled;
  return <section className="composer">
    <textarea value={props.draft} maxLength={MAX_ACTION_LENGTH} readOnly={!editable}
      onChange={(event) => props.onDraft(event.target.value)} placeholder="输入本回合角色行动…" />
    <div className="button-row">
      {props.status === "EDITING" && <>
        <button onClick={props.onSubmit} disabled={props.disabled || !props.draft.trim()}>提交行动</button>
        <button className="secondary" onClick={props.onPause} disabled={props.disabled}>暂停</button>
      </>}
      {props.status === "READY" && <>
        <span className="muted ready-note">已提交，等待对方…</span>
        <button className="secondary" onClick={props.onCancel} disabled={props.disabled}>撤销提交</button>
      </>}
      {props.status === "PAUSED" && <>
        <span className="muted">已暂停</span>
        <button onClick={props.onResume} disabled={props.disabled}>恢复</button>
      </>}
      {props.status === "PROCESSING" && <span className="muted processing-note">AI 正在生成…</span>}
    </div>
  </section>;
}
