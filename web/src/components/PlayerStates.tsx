import type { PlayerState, Role, RoleDefinition } from "../protocol";

const labels: Record<string, string> = { EDITING: "编辑中", READY: "已提交", PAUSED: "已暂停", PROCESSING: "处理中" };

export function PlayerStates({ players, roles }: {
  players: Partial<Record<Role, PlayerState>>;
  roles: RoleDefinition[];
}) {
  return <div className="player-states">{roles.map(({ id: role, name: configuredName }) => {
    const player = players[role];
    const name = player?.character_name || configuredName || role;
    const occupied = Boolean(player?.user);
    const status = player
      ? (occupied
        ? (player.connected ? (labels[player.status] ?? player.status) : "离线")
        : "无人扮演")
      : "无人扮演";
    const stateClass = player ? `state-${player.status.toLowerCase()}` : "state-empty";
    return <div className={`player-state ${stateClass}`} key={role}
      title={player?.user ? `${name} · ${player.user}` : name}>
      <span>{name} {status}</span>
    </div>;
  })}</div>;
}
