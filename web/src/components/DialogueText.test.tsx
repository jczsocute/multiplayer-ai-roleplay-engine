import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { DialogueText, segmentDialogue } from "./DialogueText";

describe("DialogueText", () => {
  it("normalizes Chinese curved quotes and highlights the complete dialogue", () => {
    const html = renderToStaticMarkup(<DialogueText text="她说：“你好。”" />);
    expect(html).toContain('class="dialogue-quote"');
    expect(html).toContain("「你好。」");
    expect(html).not.toContain("“");
  });

  it("highlights multiple dialogue spans independently", () => {
    const segments = segmentDialogue("“你好。”他说，“晚上见。”");
    expect(segments.filter((segment) => segment.dialogue).map((segment) => segment.text))
      .toEqual(["「你好。」", "「晚上见。」"]);
  });

  it("preserves existing corner quotes", () => {
    expect(segmentDialogue("「已经是这种引号」"))
      .toEqual([{ text: "「已经是这种引号」", dialogue: true }]);
  });

  it("leaves ordinary and unpaired text safe", () => {
    expect(segmentDialogue("没有对话的普通文本"))
      .toEqual([{ text: "没有对话的普通文本", dialogue: false }]);
    expect(segmentDialogue("她说：“还没说完"))
      .toEqual([{ text: "她说：「还没说完", dialogue: false }]);
  });
});
