import { Fragment } from "react";

export type DialogueSegment = { text: string; dialogue: boolean };

export function segmentDialogue(text: string): DialogueSegment[] {
  const normalized = text.replaceAll("“", "「").replaceAll("”", "」");
  const segments: DialogueSegment[] = [];
  let position = 0;

  while (position < normalized.length) {
    const opening = normalized.indexOf("「", position);
    if (opening < 0) {
      segments.push({ text: normalized.slice(position), dialogue: false });
      break;
    }
    const closing = normalized.indexOf("」", opening + 1);
    if (closing < 0) {
      segments.push({ text: normalized.slice(position), dialogue: false });
      break;
    }
    if (opening > position) {
      segments.push({ text: normalized.slice(position, opening), dialogue: false });
    }
    segments.push({ text: normalized.slice(opening, closing + 1), dialogue: true });
    position = closing + 1;
  }

  if (normalized.length === 0) return [{ text: "", dialogue: false }];
  return segments;
}

export function DialogueText({ text }: { text: string }) {
  return <>{segmentDialogue(text).map((segment, index) =>
    segment.dialogue
      ? <span className="dialogue-quote" key={index}>{segment.text}</span>
      : <Fragment key={index}>{segment.text}</Fragment>
  )}</>;
}
