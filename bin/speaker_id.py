"""Give speakers names instead of per-meeting cluster numbers.

Disagreement between the two is reported rather than silently resolved.
"""
import argparse
import collections
import datetime
import glob
import json
import os
import re
import sys

import numpy as np

import db

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
VOICE_THRESHOLD = 0.30      # cosine distance for agglomerative merge
MIN_NAME_VOTES = 3

# The board roster is small, public, and verifiable, so it is used as a
# whitelist: free-text name voting otherwise drifts onto titles and onto
# whoever was merely MENTIONED in the preceding sentence.
# Fallback only. The real roster is per-meeting and comes from the published
# agendas (see bin/roster.py) - the board turns over, and a fixed list of the
# five CURRENT commissioners applied to the whole archive put 27,175 utterances
# on people who were not seated that day and left three who were unnameable.
COMMISSIONERS = {"oakley", "weightman", "starkey", "yeager", "mariano"}


def load_rosters(con):
    """{video_id: {surname_lower, ...}} - who could possibly be speaking."""
    rosters = collections.defaultdict(set)
    for r in con.execute("""
            SELECT v.id vid, lower(p.surname) sn
            FROM meeting_roster mr
            JOIN people p   ON p.id = mr.person_id
            JOIN meetings m ON m.id = mr.meeting_id
            JOIN videos v   ON v.meeting_id = m.id"""):
        rosters[r["vid"]].add(r["sn"])
    # BOARD MEMBERS, and it has to say so. This set is "who could ever have
    # been seated": it whitelists name voting, restricts the voice anchor, and
    # decides whether a heard name is a commissioner at all.
    everyone = {r[0] for r in con.execute(
        "SELECT lower(surname) FROM people "
        "WHERE kind = 'board' AND surname IS NOT NULL")}

    # A meeting with no published agenda still has a knowable board: whoever's
    # term spans that date. Falling back to everyone let commissioners who left
    # years earlier be assigned voices.
    terms = [(r[0], r[1], r[2], r[3]) for r in con.execute(
        "SELECT lower(p.surname), bt.first_seen, bt.last_seen, bt.body "
        "FROM board_terms bt JOIN people p ON p.id = bt.person_id")]
    body_of = {r[0]: r[1] for r in con.execute(
        "SELECT v.id, COALESCE(m.body, k.body) FROM videos v "
        "LEFT JOIN meetings m ON m.id = v.meeting_id "
        "LEFT JOIN (VALUES ('bcc','Board of County Commissioners'), "
        "                  ('planning','Planning Commission')) "
        "       AS k(kind, body) ON k.kind = v.kind")}
    for r in con.execute("SELECT id, upload_date FROM videos "
                         "WHERE upload_date IS NOT NULL"):
        if r["id"] in rosters or not terms:
            continue
        body = body_of.get(r["id"])
        d = r["upload_date"]
        # Terms are bounded by the first and last agenda that named someone, so
        # widen by a term's length at each end rather than treating them as exact.
        seated = {sn for sn, lo, hi, b in terms
                  if b == body
                  and str(lo - datetime.timedelta(days=120)) <= d
                  <= str(hi + datetime.timedelta(days=400))}
        # No seated set means we do not know who could be speaking. That is a
        # reason to say nothing, not a reason to guess from another body.
        rosters[r["id"]] = seated
    return dict(rosters), (everyone or set(COMMISSIONERS))


# Titles that look like surnames after "Madam Clerk" / "Mr. Chairman".
TITLES = {"clerk", "chairman", "chairwoman", "chair", "attorney",
          "administrator", "president", "sheriff", "mayor", "county"}

# The chair rarely gets handed the floor - they do the handing - so the
# name-adjacency signal misses them entirely. They are instead the voice that
# runs procedure, which is highly distinctive language.
GAVEL = re.compile(
    r"call the roll|all in favor|signify by saying aye|point of order"
    r"|do we have a motion|any further discussion|we'll take a (recess|break)"
    r"|next item|call(?:ing)? this meeting to order", re.I)

# Recognition patterns: who is being handed the floor.
ADDRESS = re.compile(
    r"\bcommissioner\s+([A-Z][a-z]+)|\bchair(?:man|woman)?\s+([A-Z][a-z]+)"
    r"|\bmr\.?\s+([A-Z][a-z]+)|\bms\.?\s+([A-Z][a-z]+)|\bmrs\.?\s+([A-Z][a-z]+)",
    re.I)
SELF_ID = re.compile(r"\bmy name is\s+([A-Z][a-z]+(?:\s+[A-Z][a-z']+){0,2})", re.I)
# The chair is called by the clerk too, but as "Chairman X", not "Commissioner
# X" - matching only the latter is why the chair never got a name.
ROLLCALL = re.compile(
    r"district\s+\w+,?\s+(?:commissioner|chair(?:man|woman)?)\s+([A-Z][a-z]+)",
    re.I)
# The clerk queues public speakers before they reach the podium, and the
# announcement is a QUEUE: "Elaine Lance, followed by Anthony Sikhenes.
# Followed by Nancy Hazelwood." names three people in the order they will
# speak, and only the FIRST of them is about to talk.
QUEUE_SPLIT = re.compile(r"\bfollowed by\b|\band then\b", re.I)
# Internal capitals are part of the surname: McBride, DeSantis, O'Neil.
QUEUE_NAME = re.compile(r"[A-Z][a-z']+(?:[A-Z][a-z']+)*"
                        r"(?:\s+[A-Z][a-z']+(?:[A-Z][a-z']+)*){0,2}")
# This runs on EVERY utterance that precedes a handoff, so it has to be sure it
# is looking at an announcement. Without a cue, "I spoke with John Smith
# yesterday" would hand the next speaker the name John Smith.
QUEUE_CUE = re.compile(
    r"\bfollowed by\b|\band then\b|\bsigned up\b|\bon (?:the|my) list\b"
    r"|\bnext (?:person|individual|speaker|up|is|we have)\b"
    r"|\bfirst (?:person|individual|speaker|three|two)\b", re.I)
# ...except that the re-announcement before the LAST speaker in a queue is
# often nothing but the name - "And Nancy Hazelwood." - which is precisely the
# case the old pattern missed, leaving the one person it could have named
# correctly unattributed. An utterance whose entire content is a full name,
# said at a handoff, is an announcement.
QUEUE_BARE = re.compile(
    r"^\W*(?:and|then|okay|alright|next|now|uh|um)?[\s,]*"
    r"[A-Z][a-z']+(?:[A-Z][a-z']+)*(?:\s+[A-Z][a-z']+(?:[A-Z][a-z']+)*){1,2}"
    r"[\s.?!]*$", re.I)


def queue_names(text):
    """The public-comment queue an announcement names, in speaking order."""
    text = " ".join((text or "").split())
    if not (QUEUE_CUE.search(text) or QUEUE_BARE.match(text)):
        return []
    out = []
    for i, part in enumerate(QUEUE_SPLIT.split(text)):
        cands = []
        for m in QUEUE_NAME.finditer(part):
            nm = trim_name(m.group(0).strip())
            if nm and nm.split()[0].lower() not in TITLES and plausible_name(nm):
                cands.append(nm)
        if i == 0:
            # Before the first "followed by", the one about to speak is the
            # LAST name in the segment - everything earlier is lead-in ("Thank
            # you.", "Mr. Chairman Mariano.", "All right,").
            cands = [c for c in cands if len(c.split()) >= 2]
            if cands:
                out.append(cands[-1])
        elif cands:
            out.append(cands[0])
    return out


NAME_LEAD = {"an", "a", "the", "this", "that", "next", "our", "your", "my",
             "is", "was", "item", "agenda", "and", "to", "of", "for", "mr",
             "mrs", "ms", "dr"}


NAME_TRAIL = {"and", "with", "from", "for", "at", "in", "on", "of", "the", "a",
              "to", "is", "was", "my", "i", "we", "it", "that", "said", "who",
              "im", "live", "here", "speaking"}

# ASR capitalises filler at a sentence start, so it arrives as the first token
# of a name: "Uh Debbie Manns", "Um Thomas Smyers", "Okay Nancy Hazelwood".
NAME_FILLER = {"um", "uh", "er", "ah", "oh", "so", "well", "yes", "yeah",
               "okay", "ok", "alright", "actually", "right", "now", "then",
               "next", "also", "thank", "thanks", "please", "sorry", "first",
               "and"}


def trim_name(name):
    """Drop the filler and connectives the capture runs into at either end."""
    toks = name.split()
    while toks and toks[0].lower() in NAME_FILLER:
        toks.pop(0)
    while toks and toks[-1].lower() in NAME_TRAIL:
        toks.pop()
    return " ".join(toks)


def plausible_name(name):
    """Reject phrases the name patterns pick up that are not people."""
    toks = name.split()
    if not 1 <= len(toks) <= 3 or len(name) < 4:
        return False
    if toks[0].lower() in NAME_LEAD:
        return False
    return all(t[:1].isupper() and t.isalpha() and len(t) > 1 for t in toks)


def load_meeting(vid):
    d = os.path.join(DATA, vid)
    emb_path = os.path.join(d, "embeddings.npz")
    diar_path = os.path.join(d, "diarization.json")
    if not (os.path.exists(emb_path) and os.path.exists(diar_path)):
        return None
    z = np.load(emb_path, allow_pickle=True)
    turns = json.load(open(diar_path))["turns"]
    return {"labels": [str(x) for x in z["labels"]],
            "emb": z["embeddings"].astype(np.float32), "turns": turns}


def label_for_time(turns, t):
    """pyannote label whose turn covers t (nearest if none does)."""
    best, best_d = None, 1e9
    for tn in turns:
        if tn["start"] <= t <= tn["end"]:
            return tn["speaker"]
        d = min(abs(tn["start"] - t), abs(tn["end"] - t))
        if d < best_d:
            best, best_d = tn["speaker"], d
    return best


def cluster_voices(meetings, skip=None):
    """Agglomerative clustering of every speaker centroid in the archive."""
    from sklearn.cluster import AgglomerativeClustering

    skip = skip or set()
    keys, vecs = [], []
    for vid, m in meetings.items():
        for i, lab in enumerate(m["labels"]):
            if (vid, str(lab)) in skip:
                continue
            v = m["emb"][i]
            n = np.linalg.norm(v)
            if n > 0:
                keys.append((vid, lab))
                vecs.append(v / n)
    X = np.vstack(vecs)
    model = AgglomerativeClustering(
        n_clusters=None, distance_threshold=VOICE_THRESHOLD,
        metric="cosine", linkage="average")
    labels = model.fit_predict(X)
    return dict(zip(keys, labels.tolist())), len(set(labels.tolist()))


def assign_per_meeting(local_votes, local_gavel=None, rosters=None,
                       everyone=None):
    """One commissioner per voice, per meeting, via optimal matching."""
    from scipy.optimize import linear_sum_assignment

    rosters = rosters or {}
    everyone = everyone or set(COMMISSIONERS)
    by_meeting = collections.defaultdict(dict)
    for (vid, lab), ctr in local_votes.items():
        by_meeting[vid][lab] = ctr

    out = {}
    for vid, voices in by_meeting.items():
        # Only the people actually seated at THIS meeting are candidates. That
        # is the whole point: the matcher cannot invent a seat, so a voice can
        # never be assigned to someone who had not yet taken office.
        # `or everyone` here would undo load_rosters' refusal to guess: a
        # meeting with no known roster would fall straight back to every person
        # we have ever heard of, including members of an entirely different
        # board. An unknown roster means no candidates, not all candidates.
        roster = sorted(rosters.get(vid) or ())
        if not roster:
            continue
        labs = [l for l, c in voices.items()
                if any(k.lower() in roster for k in c)]
        if not labs:
            continue
        cost = np.zeros((len(labs), len(roster)), dtype=np.float64)
        for i, lab in enumerate(labs):
            ctr = voices[lab]
            for j, name in enumerate(roster):
                cost[i, j] = -ctr.get(name.title(), 0)
        rows_i, cols_i = linear_sum_assignment(cost)
        claimed = set()
        for i, j in zip(rows_i, cols_i):
            if cost[i, j] < 0:      # only keep matches with actual evidence
                out[(vid, labs[i])] = (roster[j].title(), int(-cost[i, j]))
                claimed.add(roster[j])

        # The chair is never handed the floor, so no name-vote reaches them.
        # Resolve them per MEETING, not globally: the board reorganises every
        # year, so the gavel passes to a different commissioner and a single
        # archive-wide "chair" is simply wrong.
        if local_gavel:
            left = sorted(set(roster) - claimed)   # roster is this meeting's
            if len(left) == 1:
                cands = [(local_gavel.get((vid, l), 0), l)
                         for l in voices if (vid, l) not in out]
                cands.sort(reverse=True)
                if cands and cands[0][0] >= 3:
                    out[(vid, cands[0][1])] = (left[0].title(), 0)
    return out


def consensus(per_meeting, local_to_cluster, spread, gavel, everyone=None):
    """Roll per-meeting assignments up to global voice clusters."""
    tally = collections.defaultdict(collections.Counter)
    for (vid, lab), (name, votes) in per_meeting.items():
        c = local_to_cluster.get((vid, lab))
        if c is not None:
            tally[c][name] += votes

    resolved = {}
    for c, ctr in tally.items():
        (name, n), = ctr.most_common(1)
        total = sum(ctr.values())
        if n >= MIN_NAME_VOTES and n / total >= 0.50:
            resolved[c] = (name, n, total, spread[c])

    # The chair runs procedure and is never handed the floor, so they are found
    # by gavel language and then named by elimination against the roster.
    chair = gavel.most_common(1)[0][0] if gavel else None
    if chair is not None and chair not in resolved:
        taken = {r[0].lower() for r in resolved.values()}
        left = sorted((everyone or COMMISSIONERS) - taken)
        if len(left) == 1:
            resolved[chair] = (left[0].title(), 0, 0, spread[chair])
    return resolved, chair


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true",
                    help="persist assignments to the database")
    args = ap.parse_args()

    con = db.connect()
    vids = [os.path.basename(os.path.dirname(p))
            for p in sorted(glob.glob(os.path.join(DATA, "*", "embeddings.npz")))]
    meetings = {}
    for v in vids:
        m = load_meeting(v)
        if m:
            meetings[v] = m
    print(f"{len(meetings)} meetings with voice embeddings", flush=True)

    # Voices with too little identifiable speech, plus any a human has marked
    # as not worth naming, are held out of clustering entirely.
    import triage
    unidentifiable = set()
    for vid in meetings:
        for r in con.execute(
                "SELECT local_label, string_agg(text, '\x1f') txt, "
                'SUM("end"-start) secs FROM utterances '
                "WHERE video_id=%s AND local_label IS NOT NULL "
                "GROUP BY local_label", (vid,)):
            lines = (r["txt"] or "").split("\x1f")
            if not triage.voice_is_identifiable(lines, r["secs"] or 0):
                unidentifiable.add((vid, r["local_label"]))
    try:
        ignored = {(r["video_id"], r["local_label"]) for r in con.execute(
            "SELECT video_id, local_label FROM speaker_ignore")}
    except Exception:
        ignored = set()
    skip = unidentifiable | ignored
    print(f"holding out {len(unidentifiable)} voices with too little speech"
          f"{f' + {len(ignored)} manually ignored' if ignored else ''}",
          flush=True)

    assign, n_clusters = cluster_voices(meetings, skip=skip)
    spread = collections.Counter()
    for (vid, lab), c in assign.items():
        spread[c] += 1
    recurring = [c for c, n in spread.items() if n >= 5]
    print(f"{len(assign)} centroids -> {n_clusters} voice clusters "
          f"({len(recurring)} appearing in 5+ meetings)\n", flush=True)

    # Votes are collected per (meeting, local speaker) rather than per global
    # cluster, because the decisive constraint is per-meeting: one commissioner
    # is at most one voice in any given meeting. Voting on global clusters
    # independently is what produced "Oakley in 156 meetings" out of an
    # 87-meeting corpus.
    local_votes = collections.defaultdict(collections.Counter)
    selfid = collections.defaultdict(collections.Counter)
    announced = collections.defaultdict(collections.Counter)
    gavel = collections.Counter()      # procedural language, per voice cluster
    local_gavel = collections.Counter()          # ... and per meeting voice
    reads_roll = collections.Counter()           # ... and who is the clerk
    spoke = collections.Counter()
    local_to_cluster = {}
    for vid, m in meetings.items():
        rows = con.execute(
            'SELECT idx, start, "end", speaker, text FROM utterances '
            "WHERE video_id=%s ORDER BY idx", (vid,)).fetchall()
        if not rows:
            continue
        lab_of, gid = {}, {}
        for r in rows:
            lab = label_for_time(m["turns"], (r["start"] + r["end"]) / 2)
            lab_of[r["idx"]] = lab
            gid[r["idx"]] = assign.get((vid, lab))
            if lab is not None:
                local_to_cluster[(vid, lab)] = assign.get((vid, lab))
        for i, r in enumerate(rows):
            lab, c = lab_of[r["idx"]], gid[r["idx"]]
            if lab is None:
                continue
            if GAVEL.search(r["text"]):
                local_gavel[(vid, lab)] += 1
                if c is not None:
                    gavel[c] += 1
            # Who READS the roll is the clerk, not a member of the board.
            # "District one, Commissioner Oakley. District two, Commissioner
            # Weightman." is a county employee calling names; a commissioner
            # answers it, never delivers it. This is the one signal that
            # separates the clerk from the members when both sit at the front
            # and the voiceprints have been merged into one cluster.
            if ROLLCALL.search(r["text"]):
                reads_roll[(vid, lab)] += 1
            if c is not None:
                spoke[c] += 1
            for nm in SELF_ID.findall(r["text"]):
                nm = trim_name(nm.strip().title())
                if plausible_name(nm):
                    selfid[(vid, lab)][nm] += 1
            if i == 0:
                continue
            prev = rows[i - 1]
            if lab_of[prev["idx"]] == lab:
                continue          # same voice continuing; no handoff
            tail = prev["text"][-160:]
            names = [g for m_ in ADDRESS.finditer(tail) for g in m_.groups() if g]
            names += ROLLCALL.findall(tail)
            for nm in names:
                if nm.lower() in TITLES:
                    continue      # "Madam Clerk" is a role, not a surname
                local_votes[(vid, lab)][nm.title()] += 1
            # The WHOLE previous utterance, not its last 160 characters: the
            # name of the person about to speak leads the announcement, and
            # the tail was cutting exactly the half that matters. Only the
            # head of the queue is claimed - the rest of it belongs to
            # speakers who have not stood up yet, and the clerk re-announces
            # before each of them anyway, so this is self-correcting.
            q = queue_names(prev["text"])
            if q:
                announced[(vid, lab)][q[0]] += 1

    rosters, everyone = load_rosters(con)
    print(f"rosters: {len(rosters)} meetings with a published board roster, "
          f"{len(everyone)} people who ever sat", flush=True)
    per_meeting = assign_per_meeting(local_votes, local_gavel, rosters, everyone)
    resolved, chair_cluster = consensus(per_meeting, local_to_cluster, spread,
                                        gavel, everyone)

    # Anchor pass: the text-derived assignments above become reference
    # voiceprints, and every remaining voice is matched against them. Blind
    # clustering splits each commissioner across ~15 clusters and cannot be
    # fixed by threshold; anchoring gives one identity per person and reaches
    # meetings where nobody said their name aloud.
    import anchors as A
    vecs = A.collect_vectors(meetings)
    seed = {k: v[0] for k, v in per_meeting.items()}
    for r in con.execute("SELECT video_id, local_label, name FROM speaker_label"):
        seed[(r["video_id"], r["local_label"])] = r["name"]   # human wins
    anchor_assign, rounds = A.refine(
        vecs, seed, restrict_unique=set(n.title() for n in everyone))
    print(f"\nanchor pass: {len(seed)} seeds -> {len(anchor_assign)} voices "
          f"identified (rounds: {rounds})", flush=True)

    # Per-meeting matching is the authority for coverage: count the distinct
    # meetings each name was actually matched in, which cannot exceed the
    # corpus size the way independent cluster voting could.
    mtgs_by_name = collections.defaultdict(set)
    for (vid, lab), (name, _) in per_meeting.items():
        mtgs_by_name[name].add(vid)

    by_name = collections.defaultdict(list)
    for c, r in resolved.items():
        by_name[r[0]].append((c, r))

    n_meetings = len(meetings)
    print(f"{'name':<14}{'meetings':>10}{'votes':>8}{'share':>8}{'gavel':>8}")
    print("-" * 48)
    for name in sorted(set(list(by_name) + list(mtgs_by_name)),
                       key=lambda n: -len(mtgs_by_name.get(n, ()))):
        items = by_name.get(name, [])
        v = sum(i[1][1] for i in items)
        tot = sum(i[1][2] for i in items)
        gv = sum(gavel.get(i[0], 0) for i in items)
        print(f"{name:<14}{len(mtgs_by_name.get(name, ())):>10}{v:>8}"
              f"{v/max(tot,1):>8.0%}{gv:>8}")
    print(f"(corpus is {n_meetings} meetings - none may exceed it)")

    if chair_cluster is not None:
        who = next((n for n, its in by_name.items()
                    if any(i[0] == chair_cluster for i in its)), "UNNAMED")
        print(f"\nchair by gavel language: cluster {chair_cluster} -> {who} "
              f"({gavel[chair_cluster]} procedural phrases, "
              f"{spread[chair_cluster]} meetings)")

    # The text signal, corroborated by voice before it is believed.
    local_names = {k: v for k, v in selfid.items() if v}
    for k, v in announced.items():
        if k not in local_names and v:
            local_names[k] = v
    cluster_votes = collections.defaultdict(collections.Counter)
    cluster_mtgs = collections.defaultdict(lambda: collections.defaultdict(set))
    for (vid, lab), ctr in local_names.items():
        c = assign.get((vid, lab))
        if c is None:
            continue
        for nm, k in ctr.items():
            cluster_votes[c][nm] += k
            cluster_mtgs[c][nm].add(vid)

    named_commenters, dropped = {}, 0
    for (vid, lab), ctr in local_names.items():
        c = assign.get((vid, lab))
        if c is None or spread.get(c, 1) <= 1:
            named_commenters[(vid, lab)] = ctr.most_common(1)[0][0]
            continue
        # Recurring voice: take the cluster's own majority name, and only if
        # the same name was heard for this voice in more than one meeting.
        best = cluster_votes[c].most_common(1)[0][0] if cluster_votes[c] else None
        if best and len(cluster_mtgs[c][best]) > 1:
            named_commenters[(vid, lab)] = best
        else:
            dropped += 1
    print(f"\npublic commenters named: {len(named_commenters)} voices "
          f"({len(selfid)} self-identified, "
          f"{len(set(announced) - set(selfid))} from clerk announcements); "
          f"{dropped} dropped - recurring voice, no cross-meeting agreement")

    singles = [c for c, n in spread.items() if n == 1]
    print(f"\n{len(singles)} voices appear in exactly one meeting "
          f"(public commenters), {len(named_commenters)} of them named")

    if args.write:
        # speaker_identity, speaker_label and speaker_ignore are defined in
        # bin/schema.sql. Human labels are anchored to (video_id, local_label)
        # - the stable diarization identity - NOT to cluster ids, which are
        # reshuffled on every re-clustering run (measured: only 2% keep their
        # id when new meetings arrive). Anchoring to a cluster id would
        # silently move a label onto a different person.

        # Re-propagate: a voice labelled in any meeting names the whole cluster
        # it now belongs to, so one label keeps working as clustering changes.
        human_votes = collections.defaultdict(collections.Counter)
        for r in con.execute("SELECT video_id, local_label, name "
                             "FROM speaker_label"):
            c = assign.get((r["video_id"], r["local_label"]))
            if c is not None:
                human_votes[c][r["name"]] += 1
        human = {c: v.most_common(1)[0][0] for c, v in human_votes.items()}

        # Precedence: a human label is authoritative; then the anchor match
        # (which covers meetings where no name was spoken); then a commenter
        # who introduced themselves. Cluster-level consensus is the fallback.
        # Hoisted: this was re-running a full table scan once per voice.
        labeled = {(r["video_id"], r["local_label"]) for r in con.execute(
            "SELECT video_id, local_label FROM speaker_label")}
        # The roster check belongs HERE, not only in assign_per_meeting(). The
        # anchor pass propagates identities by voice similarity and is date-blind,
        # so constraining the matcher alone spread a correctly-bounded seed over
        # meetings outside the commissioner's term and made misattribution worse,
        # 23% -> 31%. Every route to a name passes through this loop.
        rows, blocked = [], 0
        for (vid, lab), c in assign.items():
            if (vid, lab) in labeled:
                # A human said so; that outranks any roster we parsed.
                name, conf = seed[(vid, lab)], 1.0
            elif (vid, lab) in anchor_assign:
                name, conf = anchor_assign[(vid, lab)]
            elif named_commenters.get((vid, lab)):
                name, conf = named_commenters[(vid, lab)], 0.5
            elif c in resolved:
                name, conf = resolved[c][0], resolved[c][1] / max(resolved[c][2], 1)
            else:
                name, conf = None, None
            seated = rosters.get(vid) or set()
            # `and seated` used to be part of this condition, which meant the
            # guard switched itself off for exactly the meetings it was needed
            # most: the ones with no known roster. A board member's name must
            # be positively supported by a roster, never merely un-refuted.
            if (name and (vid, lab) not in labeled
                    and name.lower() in everyone and name.lower() not in seated):
                name, conf = None, None      # not on this board, that day
                blocked += 1
            rows.append((vid, lab, c, name, conf))
        print(f"roster guard: {blocked} assignments dropped - the named "
              f"commissioner was not seated at that meeting", flush=True)

        # ONE SEAT, ONE VOICE. A commissioner occupies one chair and speaks
        # with one voice in any given meeting. `assign_per_meeting` enforces
        # that while matching - it is the whole reason for the Hungarian
        # assignment - but the ANCHOR pass above is meeting-blind, matching
        # voiceprints archive-wide, so it reintroduces exactly what the matcher
        # excluded. Measured before this guard: 457 (meeting, member) pairs
        # carried two or more voices.
        seats = collections.defaultdict(list)
        for i, (vid, lab, c, name, conf) in enumerate(rows):
            if name and name.lower() in everyone:
                seats[(vid, name)].append(i)

        def support(i):
            vid, lab, _c, name, conf = rows[i]
            votes = local_votes.get((vid, lab)) or {}
            return ((vid, lab) in labeled,                    # a human said so
                    reads_roll.get((vid, lab), 0) < 2,        # not the clerk
                    per_meeting.get((vid, lab), (None,))[0] == name,
                    votes.get(name, 0) + votes.get(name.title(), 0),
                    conf or 0.0)

        split = 0
        for (vid, name), idxs in seats.items():
            if len(idxs) < 2:
                continue
            keep = max(idxs, key=support)
            for i in idxs:
                if i != keep:
                    rows[i] = (*rows[i][:3], None, None)
                    split += 1
        print(f"one seat one voice: {split} assignments dropped - the name was "
              f"already on a better-evidenced voice in that meeting", flush=True)
        with con.cursor() as cur:
            # THIS STAGE OWNS THE ROWS IT LEFT NULL, AND NOTHING ELSE. `source`
            # records which stage last decided a name, and every value except NULL
            # belongs to a stage that runs AFTER this one. The upsert used to
            # write source = NULL over every row unconditionally, so one bare
            # `refresh.sh speakers` silently reverted both of them.
            #
            # ONE EXCEPTION: a HUMAN LABEL. A label written after chair_anchor ran
            # lands on a row marked source='chair', and "never overwrite a sourced
            # row" would hold the machine's answer over a person's, which is the
            # precedence rule upside down. Those rows come back to this stage.
            mine = ("(speaker_identity.source IS NULL OR EXISTS ("
                    "SELECT 1 FROM speaker_label sl "
                    "WHERE sl.video_id = speaker_identity.video_id "
                    "AND sl.local_label = speaker_identity.local_label))")
            cur.executemany(
                "INSERT INTO speaker_identity "
                "(video_id, local_label, cluster, name, confidence, source) "
                "VALUES (%s,%s,%s,%s,%s,NULL) "
                "ON CONFLICT (video_id, local_label) DO UPDATE SET "
                "cluster=EXCLUDED.cluster, "
                f"name=CASE WHEN {mine} "
                "     THEN EXCLUDED.name ELSE speaker_identity.name END, "
                f"confidence=CASE WHEN {mine} "
                "     THEN EXCLUDED.confidence "
                "     ELSE speaker_identity.confidence END, "
                f"source=CASE WHEN {mine} THEN NULL "
                "     ELSE speaker_identity.source END", rows)

            # AND THE SAME NAMES AS EVIDENCE, WITH THE REASON ATTACHED.
            try:
                import speaker_claims
                spans = speaker_claims.runs_by_voice(con)
                made = 0
                for vid, lab, _c, nm, _conf in rows:
                    if not nm:
                        continue
                    key = (vid, lab)
                    if nm in selfid.get(key, {}):
                        how = "self"
                    elif nm in announced.get(key, {}):
                        how = "chair"
                    else:
                        how = "voice"
                    for lo, hi in spans.get(key, []):
                        speaker_claims.append(con, vid, lo, hi, nm, how,
                                              label=lab)
                        made += 1
                con.commit()
                print(f"  {made} claims recorded, with the method that named "
                      f"each voice rather than a null")
            except Exception as e:                        # noqa: BLE001
                print(f"  claims not recorded ({type(e).__name__}: {e}); "
                      f"the assignments above are unaffected")

            # RETRACT, and a voice a human has LABELLED is never retracted. The
            # two tables do not overlap today, so this spares nothing now; it is
            # here because both are human statements and one silently deleting the
            # other is not a resolution the pipeline gets to make.
            retract = skip - labeled          # membership test, per utterance
            drop = sorted(retract)
            n_retracted = 0
            if drop:
                cur.execute(
                    "DELETE FROM speaker_identity si "
                    "USING unnest(%s::text[], %s::text[]) AS d(vid, lab) "
                    "WHERE si.video_id = d.vid AND si.local_label = d.lab",
                    ([v for v, _ in drop], [lab for _, lab in drop]))
                n_retracted = cur.rowcount
        print(f"retracted {n_retracted} of {len(drop)} held-out voices from "
              f"speaker_identity ({len(ignored - labeled)} of them a human "
              f"'not a person')", flush=True)

        # Stamp each utterance with its voice cluster so the UI can resolve a
        # name without recomputing the audio-to-text alignment.
        n_utt = n_cleared = 0
        for vid, m in meetings.items():
            urows = con.execute(
                'SELECT idx, start, "end", cluster FROM utterances '
                "WHERE video_id=%s", (vid,)).fetchall()
            upd, clr = [], []
            for r in urows:
                lab = label_for_time(m["turns"], (r["start"] + r["end"]) / 2)
                c = assign.get((vid, lab))
                if c is not None:
                    # local_label too: two diarization speakers occasionally
                    # share a cluster within one meeting, and without it their
                    # lines cannot be told apart when inspecting a voice.
                    upd.append((c, lab, vid, r["idx"]))
                elif r["cluster"] is not None and (vid, lab) in retract:
                    clr.append((vid, r["idx"]))
            with con.cursor() as cur:
                cur.executemany(
                    "UPDATE utterances SET cluster=%s, local_label=%s "
                    "WHERE video_id=%s AND idx=%s", upd)
                # The other half of the retraction. `cluster` is what hands a
                # name to a line through voice_name, so a stale one left on a
                # held-out voice is how 463 utterances went on displaying a
                # name after the voice was withdrawn - the stamp only ever
                # wrote `if c is not None` and never cleared.
                cur.executemany("UPDATE utterances SET cluster=NULL "
                                "WHERE video_id=%s AND idx=%s", clr)
            n_utt += len(upd)
            n_cleared += len(clr)
        # The resolved name is derived, not stored: `voice_name` (see
        # bin/schema.sql) is a view, so a label applied through the UI takes
        # effect immediately without re-running this script. It is keyed on
        # (video_id, cluster) so that the roster guard below cannot be undone
        # at display time
        con.commit()
        named = sum(1 for r in rows if r[3])
        # Report the labels themselves and how far they propagated - `human`
        # is keyed by cluster, so printing its length made 43 labels read as 1.
        spread = sum(1 for r in rows if r[3] and r[4] == 1.0)
        print(f"\nwrote {len(rows)} assignments ({named} named), "
              f"tagged {n_utt} utterances, cleared the cluster off {n_cleared} "
              f"whose voice is held out")
        if labeled:
            print(f"{len(labeled)} human labels honoured, covering {spread} "
                  f"voices after propagation")


if __name__ == "__main__":
    sys.exit(main())
