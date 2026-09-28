import type { ClientMessage } from "./protocol";

/** Owner-only timeline controls. Permission is enforced by the server; the UI only
 * reflects the `is_host` flag the server computed for this session. */
export function shouldShowHostControls(isHost: boolean): boolean {
  return isHost;
}

export function hostControlsDisabled(state: {
  connection: string;
  processingStage: string | null;
}): boolean {
  return state.connection !== "CONNECTED" || state.processingStage !== null;
}

export function retryMessage(): ClientMessage {
  return { type: "retry" };
}

export function rollbackMessage(round: number): ClientMessage {
  return { type: "rollback", round };
}

export function retryConfirmation(): string {
  return (
    "将使用相同的玩家行动重新生成上一回合的世界更新和全部叙事。" +
    "当前正在编辑的下一回合输入将被清空。"
  );
}

export function rollbackConfirmation(round: number): string {
  return (
    `将回滚到第 ${round} 回合。\n\n` +
    `第 ${round + 1} 回合及之后的剧情和行动将被永久删除，` +
    `所有玩家会重新进入第 ${round + 1} 回合的编辑状态。`
  );
}

export function parseRollbackRound(raw: string): number | null {
  const text = raw.trim();
  if (!/^\d+$/.test(text)) return null;
  const value = Number(text);
  return value >= 1 ? value : null;
}

/** The round rollback defaults to: the last completed round, i.e. `current - 1`.
 *
 * Round 1 has nothing before it, so there is no default target. */
export function defaultRollbackRound(round: number | null): number | null {
  return round !== null && round > 1 ? round - 1 : null;
}

/** Prefill text for the target-round field. */
export function rollbackRoundInput(round: number | null): string {
  const fallback = defaultRollbackRound(round);
  return fallback === null ? "" : String(fallback);
}
