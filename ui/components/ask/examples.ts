/**
 * Questions to put in front of somebody who has not asked one yet.
 *
 * FOUR KINDS, ONE OF EACH, in a shuffled order. The kinds are the archive's
 * own shape: an outcome comes from the record, an argument comes from the
 * transcript, a case is a thread through many meetings, and a subject over
 * years needs both. Four questions that were all "what was decided" would
 * teach that this answers one kind of question.
 *
 * NO TWO ON THE SAME SUBJECT in one draw, which is why every entry carries
 * one. Impact fees appear in three pools, and drawing the decision, the
 * argument and the ten-year view of the same fee reads as a stuck page.
 *
 * WHAT "VERIFIED" CAN MEAN HERE. Not what it means for the /search examples:
 * those hit an index that returns the same rows every time, and clicking one
 * of these runs the agent fresh. What was checked is that the archive holds
 * the material to answer it, on the side the question asks about, and the
 * check is recorded per entry:
 *
 *   eval   a fixture in bin/eval_agent.py, judged against known targets
 *
 * The three `eval` entries keep their fixture wording to the letter. What the
 * harness judged is a question, not a subject, and a reworded one is a run
 * nobody has scored. Everything else is phrased the way somebody would
 * actually ask it, which is also why they do not all open with "What": a
 * column of four questions that all start the same way reads as a form.
 *   run    an answer in the `answers` table, produced with citations
 *   record decided items with outcomes, read at full title length
 *   said   passages on the subject, read, not counted
 *   case   the thread walked in /api/case: steps, bodies, terminal outcome
 */
export type AskExample = { q: string; kind: string; subject: string };

const DECIDED: AskExample[] = [
  // eval — the decision moment says only "all in favor say aye", so no
  // wording reaches it and the agent has to open the item.
  { q: "What was decided about the school zone speed cameras?",
    kind: "what was decided", subject: "speed cameras" },
  // eval — three traps: an October "no action" that reads as refusal, a
  // second case decided the same day that the obvious search misses, and a
  // permit that was drafted, discussed and taken out before adoption.
  { q: "What rules did Pasco County adopt for backyard chickens?",
    kind: "what was decided", subject: "chickens" },
  // run — answered with 5 citations: adopted 21 Aug 2024, second hearing.
  { q: "Do new homes pay more for schools than they used to?",
    kind: "what was decided", subject: "impact fees" },
  // record — a 2024 ordinance, the annual housing incentive plan, and
  // multifamily revenue bonds for named affordable developments.
  { q: "Where has the board put money toward affordable housing?",
    kind: "what was decided", subject: "affordable housing" },
  // record — the ERU utility fee rate resolution, adopted year after year,
  // plus the preliminary and final non-ad valorem assessments beside it.
  { q: "Did the board raise the stormwater rate?",
    kind: "what was decided", subject: "stormwater" },
];

const SAID: AskExample[] = [
  // run — two answers, one of which had to say that the December 2024
  // approval itself has no transcript because that meeting was not recorded.
  { q: "Who spoke about the license plate cameras?",
    kind: "what was said", subject: "cameras" },
  // said — residents at the microphone on Runnell Drive and off Green Key,
  // and a county administrator on which structures are in danger.
  { q: "How did residents describe the flooding on their streets?",
    kind: "what was said", subject: "flooding" },
  // said — a commissioner putting the single-family fee "in the seven
  // thousand plus range", another asking when the fee conversation happens.
  { q: "How much does a new home pay in impact fees?",
    kind: "what was said", subject: "impact fees" },
  // said — including what repealing the short term rental ordinance would
  // and would not do, which is the question a reader actually has.
  { q: "Did anyone ask to repeal the short term rental ordinance?",
    kind: "what was said", subject: "rentals" },
  // said — the extension explained as a four-lane arterial, and traffic at
  // 52 and 41 the first time in two years it was not backed up.
  { q: "Did the Ridge Road extension change traffic?",
    kind: "what was said", subject: "ridge road" },
];

const CASE: AskExample[] = [
  // eval — the discussion that explains why staff flipped from denial to
  // approval sits under a DIFFERENT agenda item than the rezoning itself.
  { q: "What happened to the Evans County Line 80 rezoning?",
    kind: "a case, start to finish", subject: "evans county line" },
  // case — PDE-25-7818: the Planning Commission denied staff's own
  // recommendation, then the board continued it five times and finally
  // continued it to a date uncertain. Seven steps, six of them recorded.
  { q: "Why was the HCM Hospitality rezoning never decided?",
    kind: "a case, start to finish", subject: "hcm hospitality" },
  // case — PDE-25-7781: eleven meetings, eight of them recorded, and no
  // terminal outcome yet. "It has not been decided" is a real answer.
  { q: "Has the Enclave at Livingston rezoning been decided?",
    kind: "a case, start to finish", subject: "enclave livingston" },
  // case — PDD-21-7516: approved 10 Aug 2021, seven steps across two bodies.
  { q: "Did the Kiddie Campus University rezoning pass?",
    kind: "a case, start to finish", subject: "kiddie campus" },
  // case — PDE-25-7721: approved 17 Jun 2025, seven steps, six recorded.
  { q: "When was the Little Road MPUD approved?",
    kind: "a case, start to finish", subject: "little road" },
];

const OVER_TIME: AskExample[] = [
  { q: "How has the board handled impact fees since 2023?",
    kind: "over the years", subject: "impact fees" },
  // record — the ERU rate set again in 2019, 2020 and 2021, so the answer
  // is a series rather than a single decision.
  { q: "How has the stormwater rate changed since 2019?",
    kind: "over the years", subject: "stormwater" },
  // record — trail easements accepted from 2023 on, a state shared-use
  // trail agreement in 2025, design change orders into 2026.
  { q: "Is the Orange Belt Trail being built?",
    kind: "over the years", subject: "orange belt" },
  // record — the fund transfers are the spending, meeting by meeting.
  { q: "Where has Penny for Pasco money gone?",
    kind: "over the years", subject: "penny for pasco" },
  // record — the Sea Pines flood abatement agreement withdrawn in 2018 and
  // amended through 2024, which is what "doing something about it" looks
  // like in a record.
  { q: "Has the county done anything about flooding since 2018?",
    kind: "over the years", subject: "flooding" },
];

const POOLS = [DECIDED, SAID, CASE, OVER_TIME];

const oneOf = <T,>(xs: readonly T[]): T => xs[Math.floor(Math.random() * xs.length)];

function shuffled<T>(xs: readonly T[]): T[] {
  const a = [...xs];
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

/** "Did anyone ask…" and "Did the board raise…" are the same question twice
 *  to a reader skimming the first word, so the draw treats it as a clash. */
const opener = (e: AskExample) => e.q.split(" ")[0].toLowerCase();

/**
 * One from each pool: no subject twice, and no two opening with the same
 * word, in a shuffled order.
 *
 * The pools are visited in a random order because the constraints are applied
 * greedily. Visiting them in a fixed order would let the first pool take the
 * only "Where" every time and push the same corner of the last pool onto the
 * page. Each step falls back rather than failing: subject first, then the
 * opening word, then anything, so the draw always yields four.
 *
 * Called on the server and handed down as a prop, NOT drawn inside the client
 * component: a random pick during render disagrees with the pick the server
 * already sent, and React reports that as a hydration error.
 */
export function askExamples(): AskExample[] {
  const subjects = new Set<string>();
  const openers = new Set<string>();
  const picked = shuffled(POOLS).map((pool) => {
    const free = pool.filter((e) => !subjects.has(e.subject));
    const best = free.filter((e) => !openers.has(opener(e)));
    const e = oneOf(best.length ? best : free.length ? free : pool);
    subjects.add(e.subject);
    openers.add(opener(e));
    return e;
  });
  return shuffled(picked);
}

/**
 * A fifth question, for the placeholder in the box, on none of the four
 * subjects already on screen. It was a hard-coded string, which is fine
 * until the pool draws the same question onto a card two inches below it
 * and the page looks like it only knows one question.
 */
export function askPlaceholder(shown: readonly AskExample[]): string {
  const used = new Set(shown.map((e) => e.subject));
  const free = POOLS.flat().filter((e) => !used.has(e.subject));
  return oneOf(free.length ? free : POOLS.flat()).q;
}
