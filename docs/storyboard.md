# ESAM Hackathon — Full Storyboard & Voiceover Script

## Competition: Hermes Agent Accelerated Business Hackathon (NVIDIA × Stripe × Nous Research)
## Theme: Building business at agentic speed — Debt Management with ESAM

---

## 🎬 Shot 1 — Designer: Empty Canvas, Light Theme

| | |
|---|---|
| **Image** | ESAM Designer showing no agent selected, light theme enabled. The nav bar has the E logo, DASHBOARD, EVALUATIONS links. The canvas is empty. The theme toggle shows ☀️ (light mode active). |
| **Duration** | ~4 seconds |

**Voiceover:**
> "ESAM — Evolving Software Agent Manager. It's a visual workflow designer that lets business operators build, deploy, and monitor AI-powered processes without writing code. The canvas starts empty. Every workflow begins with an intention."

---

## 🎬 Shot 2 — Agent Selected: Debt Management Pipeline

| | |
|---|---|
| **Image** | "Debt Management" selected from the agent dropdown. The full 10-step pipeline renders on the canvas — a directed flow from left to right: Assess Debt, Generate Demand Letter, Send Email, Skip Trace, Review, Process Payment, Wait for Response, Escalate, etc. |
| **Duration** | ~5 seconds |

**Voiceover:**
> "Here's the Debt Management workflow — a complete accounts receivable pipeline. Each node is a step: LLM calls for debt assessment, tool calls for skip-tracing and payment processing, human escalation points for manual review. Every step has its own identity, its own prompt, its own Delegation of Authority level."

---

## 🎬 Shot 3 — Panned Canvas: NemoClaw & Stripe

| | |
|---|---|
| **Image** | Canvas panned to reveal the right side of the pipeline showing the "Skip Trace via NemoClaw" node (green border, NVIDIA icon) and "Stripe Payment Gateway" node (purple border, Stripe logo). Natural scroll position — not zoomed out. |
| **Duration** | ~5 seconds |

**Voiceover:**
> "The pipeline integrates real services. NemoClaw — running on NVIDIA hardware — handles skip-tracing to find debtor contact information. The Stripe Payment Gateway processes secure payments. Both are configured as tool steps with their own API credentials and authority levels. The ESAM Runtime orchestrates everything."

---

## 🎬 Shot 4 — LLM Call Node: Assess Debt (Editor Open)

| | |
|---|---|
| **Image** | Double-clicked the "Assess Debt" LLM call node. The step editor is open showing the custom prompt: "Analyze the debtor's payment history...", model selection (Gemma 4 12B), system prompt, and response schema configuration. |
| **Duration** | ~5 seconds |

**Voiceover:**
> "Every LLM call step has a full editor. Define the prompt, choose the model, set the response schema. We can choose our models from the available models in the Nous Portal. The output feeds into the next step automatically."

---

## 🎬 Shot 5 — Tool Call: Stripe Payment Gateway (Elevated Authority)

| | |
|---|---|
| **Image** | The Stripe Payment Gateway step selected. Properties panel shows "Step Type: Tool Call", "Authority Level: Elevated" (red badge), API endpoint configuration, and Stripe connection details. Step label clearly says "Stripe Payment Gateway" (renamed from generic "Process Payment"). |
| **Duration** | ~5 seconds |

**Voiceover:**
> "This is the Stripe Payment Gateway step. It's a tool call with Elevated Authority — meaning it requires explicit approval before executing a payment. Every step in ESAM has a Delegation of Authority level: Auto, Elevated, or Escalated. Payments need human sign-off. Skip-tracing notices don't. The business operator sets the rules, not the AI."

---

## 🎬 Shot 6 — Step Selected + YAML Pane Open

| | |
|---|---|
| **Image** | A step is selected, properties panel populated on the right. The YAML drawer is also open, showing the YAML source of truth for the entire workflow. Both the structured properties panel AND the raw YAML are visible simultaneously. |
| **Duration** | ~4 seconds |

**Voiceover:**
> "ESAM is YAML-native. The properties panel gives you structured controls, but the YAML is the source of truth — it's stored in Git, versioned, reviewed, and deployed. You can edit in either view. The YAML file IS the workflow. No database abstraction layer, no click-ops vendor lock-in. Just YAML in Git."

---

## 🎬 Shot 7 — Architecture Diagram

| | |
|---|---|
| **Image** | Architecture slide showing the full ESAM stack. Brand bar: Hermes Agent (Nous mascot) → E logo → NVIDIA icon → Stripe wordmark. Flow: Hermes ↔ YAML ↔ Designer → Git → ESAM Runtime (E logo) → branches to NemoClaw Sandbox and Stripe Payment Gateway. White background, red borders for ESAM Runtime node. |
| **Duration** | ~6 seconds |

**Voiceover:**
> "This is how it all fits together. Hermes Agent translates natural language into YAML. The Designer gives you visual control over that YAML. Git versions and deploys it. The ESAM Runtime executes the workflow — calling NemoClaw for skip-tracing on NVIDIA hardware and Stripe for payment processing. The operator stays responsible for the loop, defining authority boundaries for every step. Built at agentic speed."

---

## 🎬 Shot 8 — Human Escalation: Wait for Customer Response

| | |
|---|---|
| **Image** | The "Wait for Customer Response" step selected. Properties show: Step Type — Human Escalation, Authority Level — Elevated. The prompt panel shows the message template sent to the debtor. The tag shows "manual intervention required". |
| **Duration** | ~4 seconds |

**Voiceover:**
> "Some steps need a human in the loop. Wait for Customer Response pauses the workflow until the debtor replies, or until a configurable timeout triggers an escalation. This is the Delegation of Authority principle in action — the AI handles what it can, escalates what it shouldn't. Human responsible FOR the loop, not IN it."

---

## 🎬 Final Frame — Title Card

| | |
|---|---|
| **Text** | ESAM — Evolving Software Agent Manager · Building business at agentic speed · Built with Hermes Agent · NVIDIA · Stripe |
| **Duration** | ~3 seconds |

**Voiceover:**
> "ESAM — building business at agentic speed."

---

## Total Runtime: ~38 seconds

## Transition Notes
- Each shot fades in over 0.5s, holds for the voiceover duration, fades out over 0.3s
- Cursor follows the elements being described (selecting agents, clicking nodes, scrolling canvas)
- Architecture slide gets the longest hold (6s) to allow eye-tracking across all elements
- No navigation buttons — all auto-advance
- Background: white/light throughout

## Approved Screenshots

| Shot | File | Content |
|------|------|---------|
| 1 | `approved-shot-opening-screen.png` | Empty designer, no agent selected, light theme |
| 2 | `approved-shot-debt-management-flow.png` | Full Debt Management pipeline rendered on canvas |
| 3 | `approved-shot-debt-management-flow.png` (panned right) | NemoClaw skip-tracing + Stripe Payment Gateway nodes |
| 4 | `approved-shot-llm-editor.png` | Every LLM call step — editor open, model selection from Nous Portal |
| 5 | `approved-shot-stripe-elevated.png` | Stripe Payment Gateway — Elevated Authority |
| 6 | `approved-shot-yaml-editor.png` | Step selected + YAML pane open side-by-side |
| 7 | `approved-architecture-slide.png` | Architecture: Hermes → YAML → Designer → Git → ESAM Runtime → NemoClaw + Stripe |
| 8 | `approved-shot-wait-response.png` | Wait for Customer Response — human escalation step |

## Dependencies
- Screenshots: `current/approved-shot-*.png`
- Architecture slide: `current/approved-architecture-slide.png`
- Audio: TTS engine (NeuTTS local or OpenAI TTS via Nous subscription)
- Video: ffmpeg concat with per-shot timing + fade transitions
