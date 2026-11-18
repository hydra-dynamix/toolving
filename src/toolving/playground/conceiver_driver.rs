// conceiver-session: drives one Conceiver trie for the toolving playground.
//
// Built against a conceiver crate by toolving.playground.conceiver (rustc with
// `--extern conceiver=...`); it is not part of the Python package's code.
//
// One request per stdin line, fields separated by tabs; one JSON object per
// stdout line in reply:
//   ask<TAB>line              the line's answer, its steps and their evidence
//   teach<TAB>line<TAB>answer input the line, then send the answer as its outcome
//   state<TAB>n               the trie's size and the newest n log entries
//   reset                     a freshly loaded trie
//
// `ask` and `teach` follow conceiver/tests/session.rs (skeleton-walk-only):
// a line is asked, then its correction taught on the line's own receipt.
// What counts as an answer, a step or a hypothesis is decided here and
// nowhere else, so a change to Conceiver's answers changes only this file.

use conceiver::{recount, Answer, Candidate, Fault, Found, Side, Trie};
use std::io::{self, BufRead, Write};

fn quoted(text: &str) -> String {
    let mut out = String::from("\"");
    for c in text.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
    out
}

fn fault_name(fault: &Fault) -> &'static str {
    match fault {
        Fault::Nothing { .. } => "Nothing",
        Fault::Limit { .. } => "Limit",
        Fault::Rejected { .. } => "Rejected",
    }
}

fn below(answer: &Answer, candidate: &Candidate) -> bool {
    candidate
        .found
        .receipt
        .starts_with(&format!("{} ", answer.input.receipt))
}

fn answered(candidate: &Candidate) -> &str {
    candidate.runs.last().map_or("", String::as_str)
}

// One shown candidate: an answer below the line's construction, a step the
// walk resolved (a taught pair, spelled), or a hypothesis it used.
fn step(t: &Trie, answer: &Answer, candidate: &Candidate) -> String {
    let found = &candidate.found;
    let (kind, text) = match (below(answer, candidate), found.receipt.starts_with(' ')) {
        (true, _) => ("answer", answered(candidate).to_string()),
        (false, true) => ("step", t.spell(&found.receipt).replace('\0', "\u{2400}")),
        (false, false) => ("hypothesis", String::new()),
    };
    format!(
        "{{\"kind\":{},\"text\":{},\"support\":{},\"against\":{}}}",
        quoted(kind),
        quoted(&text),
        found.evidence.support,
        found.evidence.against
    )
}

fn ask(t: &mut Trie, line: &str) -> String {
    let before = t.opened();
    let answer = match t.input(line) {
        Ok(answer) => answer,
        Err(fault) => return format!("{{\"kind\":\"fault\",\"fault\":{}}}", quoted(fault_name(&fault))),
    };
    let steps: Vec<String> = answer.candidates.iter().map(|c| step(t, &answer, c)).collect();
    let answers: Vec<Candidate> = answer
        .candidates
        .iter()
        .filter(|candidate| below(&answer, candidate))
        .cloned()
        .collect();
    let picked = match &answers[..] {
        [] => None,
        [only] => Some(only.clone()),
        [zero, one] => t
            .select(line, [zero.runs[1].as_str(), one.runs[1].as_str()])
            .ok()
            .map(|picked| picked.candidates[0].clone()),
        _ => None,
    };
    let hypothesis = answer
        .candidates
        .iter()
        .filter(|candidate| !below(&answer, candidate))
        .filter(|candidate| !candidate.found.receipt.starts_with(' '))
        .map(|candidate| candidate.found.evidence.support)
        .max()
        .unwrap_or_default();
    let support = picked
        .as_ref()
        .map_or(0, |candidate| candidate.found.evidence.support)
        .max(hypothesis);
    let recounted = recount(t.log(), &answer.input);
    let input = input_json(t, &answer.input, answer.hit, recounted.agrees, t.opened() - before);
    match picked {
        Some(candidate) => format!(
            "{{\"kind\":\"answer\",\"output\":{},\"support\":{},\"candidates\":{},\"steps\":[{}],\"input\":{}}}",
            quoted(answered(&candidate)),
            support,
            answers.len(),
            steps.join(","),
            input
        ),
        None => format!(
            "{{\"kind\":\"abstain\",\"candidates\":{},\"steps\":[{}],\"input\":{}}}",
            answers.len(),
            steps.join(","),
            input
        ),
    }
}

fn input_json(t: &Trie, found: &Found, hit: bool, agrees: bool, opened: u64) -> String {
    format!(
        "{{\"seen_before\":{},\"place\":{},\"support\":{},\"against\":{},\"recount_agrees\":{},\"opened\":{}}}",
        hit,
        quoted(&t.spell(&found.receipt).replace('\0', "\u{2400}")),
        found.evidence.support,
        found.evidence.against,
        agrees,
        opened
    )
}

fn teach(t: &mut Trie, line: &str, outcome: &str) -> String {
    match t.input(line).and_then(|answer| t.outcome(&answer.runs, outcome)) {
        Ok(answer) => format!("{{\"taught\":true,\"known\":{}}}", answer.hit),
        Err(fault) => format!("{{\"taught\":false,\"fault\":{}}}", quoted(fault_name(&fault))),
    }
}

fn state(t: &Trie, taught: &[(String, String)], newest: usize) -> String {
    let log = t.log();
    let entries: Vec<String> = log
        .iter()
        .rev()
        .take(newest)
        .map(|entry| {
            format!(
                "{{\"place\":{},\"side\":{},\"episode\":{},\"at\":{}}}",
                entry.place,
                quoted(match entry.side {
                    Side::For => "for",
                    Side::Against => "against",
                }),
                entry.episode,
                quoted(&t.spell(&entry.address).replace('\0', "\u{2400}"))
            )
        })
        .collect();
    let pairs: Vec<String> = taught
        .iter()
        .map(|(line, outcome)| format!("{{\"question\":{},\"answer\":{}}}", quoted(line), quoted(outcome)))
        .collect();
    format!(
        "{{\"nodes\":{},\"log_entries\":{},\"opened\":{},\"taught\":[{}],\"log\":[{}]}}",
        t.size(),
        log.len(),
        t.opened(),
        pairs.join(","),
        entries.join(",")
    )
}

fn main() {
    let mut t = Trie::load();
    let mut taught: Vec<(String, String)> = Vec::new();
    let stdout = io::stdout();
    let mut out = stdout.lock();
    writeln!(out, "{{\"ready\":true,\"nodes\":{}}}", t.size()).unwrap();
    out.flush().unwrap();
    for request in io::stdin().lock().lines() {
        let request = request.expect("stdin is text");
        let fields: Vec<&str> = request.split('\t').collect();
        let reply = match fields[..] {
            ["ask", line] => ask(&mut t, line),
            ["teach", line, outcome] => {
                let reply = teach(&mut t, line, outcome);
                if reply.starts_with("{\"taught\":true") {
                    taught.push((line.to_string(), outcome.to_string()));
                }
                reply
            }
            ["state", newest] => state(&t, &taught, newest.parse().unwrap_or(50)),
            ["reset"] => {
                t = Trie::load();
                taught.clear();
                format!("{{\"reset\":true,\"nodes\":{}}}", t.size())
            }
            _ => format!("{{\"error\":{}}}", quoted("unknown request")),
        };
        writeln!(out, "{reply}").unwrap();
        out.flush().unwrap();
    }
}
