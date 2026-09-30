# World update

Use the previous `world_state`, current world setting, character definitions, and all actions in this round to update the single objective world state. Determine what actually happens without changing a character's choices for dramatic effect. Each action is an attempt; its outcome depends on world facts and the other actions.

From the updated world state, generate a separate `character_view` for every character. Include only what that character can observe, know, or reasonably infer. Do not expose another character's private thoughts or hidden facts. Generate `character_status` only for characters with a status schema; do not add status data for others.

Follow the JSON output example and field descriptions at the end of the input exactly. Include every required field and role, and add no unknown fields. Do not write literary narration or choose a character's next action. Output valid JSON only, with top-level `world_state`, `character_views`, and `character_status`.
