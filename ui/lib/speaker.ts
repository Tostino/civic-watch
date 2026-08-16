import type { DividedInRoom, Line, TranscriptHit } from "@/lib/types";
import type { SpeakerChipProps } from "@/components/SpeakerChip";

/**
 * THE WAY TO RENDER A SPEAKER. `<SpeakerChip {...speakerOf(x)} />`, whatever x
 * is.
 *
 * The archive says the same four things in three different spellings, because
 * the three shapes grew at different times: a `Line` calls them name /
 * display_name / human / basis, a `TranscriptHit` calls them speaker /
 * speaker_display / name_human / name_basis, and a `DividedInRoom` splits the
 * difference. A caller had to know which convention this particular object
 * followed, and got no help if it guessed - the fields are all optional-ish
 * and a wrong one reads as "no name" rather than as an error.
 *
 * Worse, the rule that `(exchange)` is an internal token and never a label
 * (R6.2.1) was written out separately in Hits.tsx and Answer.tsx. Two copies
 * of a rule that exists to be in one place.
 *
 * So the mapping lives here, next to the component that consumes it, and a
 * new field is added in one file rather than four.
 */
export function speakerOf(x: Line | TranscriptHit | DividedInRoom): SpeakerChipProps {
  if ("speaker" in x) {
    // `speaker` is the KEY and `(exchange)` is a value only it carries. A
    // passage with no speaker at all is a different fact and stays unknown.
    const several = x.speaker === "(exchange)";
    return {
      name: several ? null : x.speaker,
      displayName: x.speaker_display,
      human: ("name_human" in x ? x.name_human : x.human) ?? false,
      basis: "name_basis" in x ? x.name_basis : x.basis,
      several,
    };
  }
  return {
    name: x.name,
    displayName: x.display_name,
    human: x.human,
    basis: x.basis,
    contested: x.contested,
  };
}
