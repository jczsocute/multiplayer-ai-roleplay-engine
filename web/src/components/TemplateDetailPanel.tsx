import { templateDetailLine, templateRoleCount } from "../lobby";
import type { TemplateItem } from "../types";

type Props = {
  template: TemplateItem;
  onUse: (template: TemplateItem) => void;
};

/** Read the payload's role names as ``P1 · 名字`` rows. */
function roleRows(template: TemplateItem): Array<{ id: string; name: string }> {
  return (template.role_names ?? []).map((name, index) => ({
    id: `P${index + 1}`,
    name,
  }));
}

/** Template detail: name is the overlay title, then public metadata only. */
export function TemplateDetailPanel({ template, onUse }: Props) {
  const tags = template.tags ?? [];
  const introduction = (template.introduction ?? "").trim();
  const roles = roleRows(template);

  return <div className="panel-form template-detail">
    <p className="muted detail-line">{templateDetailLine(template)}</p>

    {tags.length > 0 && <ul className="tag-list">
      {tags.map((tag) => <li className="tag-chip" key={tag}>{tag}</li>)}
    </ul>}

    {introduction && <section className="detail-section">
      <h3>剧本介绍</h3>
      <p className="detail-text">{introduction}</p>
    </section>}

    {roles.length > 0 && <section className="detail-section">
      <h3>角色（{templateRoleCount(template)}）</h3>
      <ul className="detail-roles">
        {roles.map((role) => <li key={role.id}>
          <span className="role-id">{role.id}</span> · {role.name}
        </li>)}
      </ul>
    </section>}

    <div className="panel-actions">
      <button className="compact-button" onClick={() => onUse(template)}>使用剧本</button>
    </div>
  </div>;
}
