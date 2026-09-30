import { describe, expect, it } from "vitest";
import { initialLanguage, localizedRoomMessage, saveLanguage, translateUi } from "./i18n";
import { inviteLink, invitedRoom } from "./invite";
import { starterPreset } from "./components/PlatformHome";
import { createRoomPayload } from "./platform";
import type { TemplateItem } from "./types";

describe("first run language", () => {
  it("defaults from browser language and persists a switch", () => {
    expect(initialLanguage(null, "zh-CN")).toBe("zh");
    expect(initialLanguage(null, "en-US")).toBe("en");
    expect(initialLanguage("zh", "en-US")).toBe("zh");
    let saved = "";
    saveLanguage("en", { setItem: (key, value) => { expect(key).toBe("rp.language"); saved = value; } });
    expect(initialLanguage(saved, "zh-CN")).toBe("en");
  });

  it("localizes ordinary UI and server system messages without changing story content", () => {
    expect(translateUi("加入房间", "en")).toBe("Join Room");
    expect(translateUi("输入本回合角色行动…", "en")).toBe("Enter this character's action…");
    expect(translateUi("Player narration: 打开门", "en")).toBe("Player narration: 打开门");
    expect(localizedRoomMessage({ text: "Alice 已加入房间。", message_key: "user_joined",
      params: { name: "Alice" } }, "en")).toBe("Alice joined the room.");
    expect(localizedRoomMessage({ text: "Alice: 我来了" }, "en")).toBe("Alice: 我来了");
  });
});

describe("invite and starter entry", () => {
  it("keeps room code through the login URL and never includes password", () => {
    const url = inviteLink("https://host.example", "ab12cd");
    expect(url).toBe("https://host.example/?room=AB12CD");
    expect(invitedRoom(new URL(url).search)).toBe("AB12CD");
    expect(url).not.toContain("password");
  });

  it("prefills the existing create-room payload from a starter", () => {
    const template = { id: "tmpl_START", name: "Rock, Paper, Scissors",
      owner_user_id: 1, is_public: false, tags: ["starter"] } satisfies TemplateItem;
    const preset = starterPreset(template);
    expect(preset).toEqual({ source: "template", id: template.id, name: template.name });
    expect(createRoomPayload(preset.source, preset.id, "", preset.name)).toEqual({
      source: "template", template_id: template.id, game_name: template.name, password: "",
    });
  });
});
