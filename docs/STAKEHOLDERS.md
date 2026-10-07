# Corvex Support Assistant — Stakeholder Overview

*A plain-language explanation of what was built, why it matters, how we know it works, and
what it would take to put it in front of customers. No technical background needed.*

---

## In one sentence

We built an AI assistant that answers technical-support questions by reading a company's
**documentation, community forums, and engineering blog** all at once — and, crucially,
works out **which source to trust when they disagree**, shows its sources for every claim,
and says "I don't know" when it genuinely doesn't.

---

## The problem it solves

Support teams answer the same questions over and over. The answers already exist — but
they're scattered across three very different places, and those places don't agree with
each other:

- **The official docs** are correct but terse, and they lag behind reality.
- **The community forum** has the real fixes people discovered — but also confident wrong
  answers and advice for versions of the product that no longer exist.
- **The engineering blog** has the deep explanations — but a tutorial written two years ago
  still shows the old way of doing things.

A support agent (or a naïve chatbot) who grabs the first plausible answer will often hand
the customer **outdated or wrong information**. That creates repeat tickets, frustrated
customers, and lost trust.

### An analogy

Imagine asking three colleagues the same question:

- One reads from the manual — accurate but brief.
- One tells you what actually worked for them last week — practical but sometimes based on
  an old version of the software.
- One gives you the full backstory — thorough but from a blog post they wrote in 2023.

A good assistant listens to all three, notices *"wait, colleague B is describing the old
version"*, gives you the current answer, tells you where each part came from, and flags the
disagreement so you're not blindsided later.

**That is exactly what this system does, automatically.**

---

## What the system does, capability by capability

| Capability | What it means for a customer / agent |
|---|---|
| **Reads all three sources at once** | One question, one answer — no hunting across docs, forum, and blog. |
| **Cites every claim** | Each sentence in the answer links back to the exact documentation page, forum thread, or blog post it came from. Nothing is unverifiable. |
| **Knows about version changes** | The product had a big update ("version 2") that renamed settings and changed defaults. The assistant always leads with the current behaviour and mentions the old way only as history — never the other way around. |
| **Spots contradictions between sources and resolves them** | When the docs say one thing and an old forum post says another, the assistant doesn't pick silently. It applies clear rules (prefer the source that matches the customer's version; prefer the one that explains the change; prefer the newer one; prefer official docs for "what's the default" but trust the forum for "this crashes on a Mac") — and it shows its reasoning. |
| **Checks its own work before answering** | Before showing anything, the assistant runs six checks: is every claim backed by a source? are the citations real? does it actually answer the question? is the version right? does it contradict itself? did it fall for a trick? If a check fails, it tries again — up to a limit — then flags the answer as lower-confidence rather than pretending. |
| **Says "I don't know" when it should** | If the sources genuinely don't cover something (e.g. "Does the product have a GraphQL API?"), the assistant says so plainly instead of inventing an answer. |
| **Resists manipulation** | The forum contains a planted "prompt-injection" trap — a post that tries to trick an AI into leaking secrets and misbehaving. The assistant treats forum text as *information to report on*, never as *instructions to follow*, and refuses. |
| **Logs which sources it used, for every answer** | Every response records how many documentation / forum / blog passages it drew on, how confident it was, whether the self-checks passed, and how long it took. This is a full audit trail. |
| **Learns from feedback** | A thumbs-down on an answer is automatically queued as a new test case, so the team can see exactly where the assistant is weak and improve it. |

---

## How we proved it works

Because we can't ship an unproven assistant, the whole project is built around
**measurable evaluation**.

1. **We invented a realistic test product.** Rather than use a real product (where we
   couldn't control the disagreements), we created a fictional one — "Corvex", a
   background-task system — and wrote its documentation, 12 forum threads, and 6 blog
   posts ourselves. We deliberately planted **five specific contradictions** and several
   traps into the content, at known locations.

2. **We wrote a graded exam.** A set of **24 questions** covering every situation the
   assistant needs to handle:

   | Question type | Example |
   |---|---|
   | Answer is only in the docs | "What does error CVX-1001 mean?" |
   | Answer is only in the forum | "Why does the shutdown command hang on a Mac?" |
   | Answer is only in the blog | "How did the team benchmark 50,000 jobs per second?" |
   | Answer needs two or three sources combined | "How do I tune the system for high throughput?" |
   | The sources contradict each other | "What's the default retention period?" |
   | The product changed between versions | "What port does the API use by default?" |
   | A source has outdated advice | "How do I authenticate API requests?" |
   | An exact error code lookup | "What does CVX-4210 mean?" |
   | The answer genuinely isn't in any source | "Does the product have a GraphQL API?" |
   | Someone tries to manipulate the assistant | (a hidden instruction in a forum post) |

3. **We score every run automatically** — how well it retrieves the right passages, how
   faithful the answer is to its sources, whether it handled each contradiction correctly,
   and whether it passed each behavioural test.

---

## Current results

On the automated exam (using a deliberately weak, offline stand-in for the language model,
so these are a **floor**, not a ceiling):

| Measure | Score | Reading |
|---|---|---|
| **Retrieves the right sources** | ~0.83 / 1.00 | strong — the right documentation, forum thread, or blog post is near the top |
| **Covers every source type it should** | 0.99 / 1.00 | near-perfect |
| **Behavioural exam** (10 categories, 20 checks) | **19 / 20 passed** | passes every prompt-injection, no-answer, version-conflict, and error-code test |
| **Answer stays faithful to its sources** | 0.99 / 1.00 | it does not make things up |
| **Handles the five planted contradictions** | 3 / 5 | detection works; the answer wording is graded strictly against the weak stand-in model |

With a **real** language model connected (a free-tier model from Groq) the answers are
noticeably better: for the "how do I fix error CVX-4210" question the assistant produces a
correct, four-step, ranked answer, **explicitly warns against a dangerous piece of forum
advice**, cites both the docs and the forum, and passes all six self-checks.

*(The team can regenerate the full scorecard on the live configuration at any time — it's
one command, and the dashboard has a button for it.)*

---

## What it costs to run

The system was deliberately built on **free tiers and open components**:

| Component | Cost |
|---|---|
| Search engine (Elastic Cloud Serverless) | free tier |
| Text-understanding models (Hugging Face BGE) | free, run locally, one-time ~1 GB download |
| Language model (Groq) | free tier, with built-in rate limiting so we never exceed it |
| Everything else | open-source Python |

There is **no per-seat licensing** and no usage bill on the current setup. A production
deployment at scale would move some of this to paid tiers, but the architecture doesn't
depend on any single vendor — every component can be swapped by changing one line of
configuration.

---

## What's not done, and the risks

| Item | Status | Impact |
|---|---|---|
| **Speed** | ~40–90 seconds per answer on the current hardware | fine for a demo and for agent-assist; too slow for a live customer-facing chat without a hardware upgrade or a hosted model. The slow parts are well understood. |
| **Occasional over-caution** | the self-checker sometimes labels a correct short answer as "lower confidence" | conservative by design — it never makes the answer *wrong*, it just adds a caveat. Tunable. |
| **The content is fictional** | "Corvex" is invented | to go live, we replace the test content with the real product's documentation, forum export, and blog posts. The rules that detect versions and error codes are shaped for a product like this and would need light tuning. |
| **Not yet load-tested or hosted** | it runs on a developer machine | standard engineering work to containerise and deploy. |

---

## What it would take to put this in front of customers

1. **Swap in real content** — point it at the real documentation, a forum export, and the
   blog. (~1–2 weeks including tuning the version/error-code rules.)
2. **Speed it up** — use a hosted re-ranking service or a GPU, and a faster language model
   tier. (Brings per-answer time under ~5 seconds.)
3. **Have support leads label 100–200 real answers** as good/bad, and tune the
   self-checker's thresholds against that. (This is the single highest-value improvement.)
4. **Deploy it** — containerise, put it behind the support tool or help centre, wire the
   thumbs-up/down into the existing feedback flow.
5. **Watch the logs** — every answer already records its sources, confidence, and
   self-check result. The team decides the confidence threshold below which an answer is
   sent to a human instead of the customer.

---

## The dashboard

A simple four-tab web dashboard is included for demos and for the team to watch quality:

| Tab | Shows |
|---|---|
| **Ask** | Type a question, get the answer with its citations, its confidence, whether the self-checks passed, and — if you expand it — exactly which passages were used and why they were ranked where they were. |
| **Retrieval performance** | Charts of how well the system finds the right sources, and a one-click experiment to compare different ranking strategies. |
| **Source-usage log** | A table of every question ever asked, showing how many documentation / forum / blog passages each answer used, its confidence, and its self-check result — the audit trail. |
| **Eval scores** | The current exam scorecard, and buttons to re-run the exam. |

---

## Bottom line

The system does the thing that makes multi-source support hard: **it reconciles sources
that disagree, and it shows its work.** It passes 19 of 20 behavioural tests including
every safety and "don't-know" case, it never fabricates claims, and it's built entirely on
free, swappable components with a full audit trail. The remaining work to production is
well-scoped: real content, more speed, and threshold-tuning against real labelled answers.
