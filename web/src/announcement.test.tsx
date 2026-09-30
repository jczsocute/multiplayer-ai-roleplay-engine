import { renderToStaticMarkup } from "react-dom/server";
import ReactMarkdown from "react-markdown";
import { describe, expect, it } from "vitest";
import { markAnnouncementSeen } from "./announcement";

describe("first Lobby announcement", () => {
  it("records each user independently in browser storage", () => {
    const seen = new Map<string, string>();
    const storage = {
      getItem: (key: string) => seen.get(key) ?? null,
      setItem: (key: string, value: string) => { seen.set(key, value); },
    };
    expect(markAnnouncementSeen(1, storage)).toBe(true);
    expect(markAnnouncementSeen(1, storage)).toBe(false);
    expect(markAnnouncementSeen(2, storage)).toBe(true);
    expect(seen.get("ai-rp-announcement-seen:1")).toBe("1");
  });
});

describe("announcement Markdown", () => {
  it("renders formatting without executing raw HTML", () => {
    const html = renderToStaticMarkup(
      <ReactMarkdown>{"# 公告\n\n**欢迎**\n\n<script>alert('x')</script>"}</ReactMarkdown>,
    );
    expect(html).toContain("<h1>公告</h1>");
    expect(html).toContain("<strong>欢迎</strong>");
    expect(html).not.toContain("<script>");
  });
});
