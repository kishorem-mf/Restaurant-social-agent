Below is a **revised requirement document** that **relaxes the “strict BI” constraint** while still **eliminating post-level hallucination**.
The design explicitly allows **conversational insights and trend discussion**, but enforces **traceability whenever concrete claims are made**.

This version is suitable to hand directly to your **Claude / Bedrock implementation team**.

---

# Requirement Document

## Conversational Analytics Agent with Traceable Post Evidence

*(Hybrid: Guardrails + Agent Orchestration)*

---

## 1. Problem Statement

### 1.1 Observed Issue

The agent hallucinates **specific post-level information** (metrics, themes, rankings) when:

* No relevant posts are retrieved
* Only partial data is available

This undermines trust, even though **high-level conversational insights are desired**.

---

### 1.2 New Business Requirement

The agent **must not behave like a rigid BI system**.

It **should**:

* Discuss trends, patterns, and insights conversationally
* Provide strategic interpretations and guidance

It **must**:

* Reference **actual posts from the database** whenever making factual or concrete claims
* Avoid inventing posts, metrics, or examples

---

## 2. Design Objective

Create a **conversational intelligence agent** that:

* Speaks naturally about trends and insights
* Anchors factual claims to **real posts**
* Separates **interpretation** from **evidence**
* Clearly distinguishes **known facts** from **general observations**

---

## 3. Core Design Principle

> **Conversation is allowed.
> Interpretation is allowed.
> Fabrication is not allowed.
> Facts must be traceable.**

---

## 4. Target Architecture (Hybrid)

| Layer               | Responsibility                                |
| ------------------- | --------------------------------------------- |
| Agent Orchestration | Data awareness & evidence tracking            |
| Bedrock Guardrails  | Enforce traceability & block fake facts       |
| LLM (Claude)        | Conversational reasoning & insight generation |

---

## 5. Agent Orchestration Requirements

### 5.1 Evidence-Aware Context Injection

Before invoking the LLM, orchestration **must attach evidence metadata**:

```json
{
  "posts_retrieved": true | false,
  "post_ids": ["p123", "p456"],
  "evidence_level": "none | partial | sufficient",
  "query_scope": "posts | hashtags | creators"
}
```

---

### 5.2 Orchestration Decision Rules

#### Rule O-1: Insight Without Evidence (Allowed)

If:

```
posts_retrieved = false
```

Then:

* The agent **may discuss general trends**
* The agent **must not reference specific posts, metrics, or rankings**
* The response must include a qualifier:

  * “Based on general patterns observed in similar datasets…”

---

#### Rule O-2: Evidence-Backed Claims (Required)

If:

* Engagement numbers
* Post rankings
* Caption themes
* Examples of posts

Are included in the response →
Then:

* `posts_retrieved = true`
* At least one **post reference must be cited**

---

#### Rule O-3: Partial Evidence Handling

If:

```
evidence_level = partial
```

Then:

* The agent may describe **directional trends**
* The agent must clearly state limitations:

  * “This is based on a limited subset of posts…”

---

## 6. Bedrock Guardrails (Critical)

### 6.1 Factual Claim Classification

Guardrails must classify statements into:

| Type              | Allowed Without Evidence |
| ----------------- | ------------------------ |
| Strategic advice  | Yes                      |
| High-level trends | Yes (with qualifiers)    |
| Numeric metrics   | No                       |
| Post examples     | No                       |
| Rankings          | No                       |

---

### 6.2 Traceability Enforcement Policy

If a response contains:

* Post-level examples
* Metrics (likes, comments, averages)
* Rankings or “top” claims

Then:

* A valid post reference must exist
* Otherwise, block and force correction

---

### 6.3 Mandatory Evidence Format

When posts are referenced, the agent **must cite them explicitly**:

| Post ID | Creator | Caption Snippet | Likes |
| ------- | ------- | --------------- | ----- |

No post reference → no factual claim allowed.

---

### 6.4 Conversational Safety (Not Over-Restrictive)

Guardrails **must not block**:

* Exploratory thinking
* Hypothesis framing
* Strategic questions
* Marketing recommendations

As long as they are **not presented as facts**.

---

## 7. LLM Instruction Model (Balanced)

### 7.1 Allowed Reasoning Modes

The LLM may:

* Infer patterns from retrieved data
* Generalize carefully with qualifiers
* Offer marketing or content strategy advice

---

### 7.2 Required Language Cues

| Scenario        | Required Language            |
| --------------- | ---------------------------- |
| No evidence     | “Based on general patterns…” |
| Partial data    | “From the available posts…”  |
| Strong evidence | “The data shows…”            |

---

### 7.3 Prohibited Behaviors

The LLM must not:

* Invent post captions
* Invent metrics
* Use “for example” unless a real post is cited

---

## 8. Response Structure (Flexible but Disciplined)

### Required Sections

1. **Summary** – conversational takeaway
2. **Insights** – strategic interpretation
3. **Evidence (if applicable)** – real post references
4. **Confidence Notes** – data limitations (optional)

---

## 9. Example Behavior (Correct)

**User**: “What content trends are working?”

**Acceptable Response**:

> Dish-focused visuals tend to attract higher engagement based on general food content patterns.
> In the retrieved dataset, this is supported by posts such as:

| Post ID | Creator     | Likes  |
| ------- | ----------- | ------ |
| p341    | @chef_mario | 12,456 |

> This is a limited sample, but it aligns with broader industry behavior.

---

## 10. Success Criteria

The system is successful when:

* Conversations feel natural, not robotic
* Strategic insights are freely offered
* Every factual claim can be traced to real posts
* Users can audit claims when needed
* Hallucinated post examples disappear entirely

---

## 11. Final Position

> **This agent is not a BI tool.
> It is a conversational intelligence system with evidence discipline.
> Insights may flow freely—but facts must be earned.**

---

If you want next, I can:

* Define **guardrail classification rules** for “insight vs fact”
* Provide **example guardrail policies**
* Create **test prompts that validate conversational freedom without hallucination**

Tell me how deep you want to go next.



{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "AmazonBedrockAgentBedrockFoundationModelPolicyProd",
            "Effect": "Allow",
            "Action": [
                "bedrock:InvokeModel",
                "bedrock:InvokeModelWithResponseStream",
                "bedrock:ListInvocations",
                "bedrock:ListSessions",
                "bedrock:GetSession",
                "bedrock:RenderPrompt",
                "bedrock:CreateInvocation",
                "bedrock:CreateSession"
            ],
            "Resource": [
                "arn:aws:bedrock:*::foundation-model/anthropic.claude-3-haiku-20240307-v1:0",
                "arn:aws:bedrock:*::foundation-model/anthropic.claude-3-5-sonnet-20240620-v1:0",
                "arn:aws:bedrock:*::foundation-model/anthropic.claude-sonnet-4-20250514-v1:0"
            ]
        }
    ]
}


{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "AmazonBedrockAgentRetrieveKnowledgeBasePolicyProd",
            "Effect": "Allow",
            "Action": [
                "bedrock:Retrieve"
            ],
            "Resource": [
                "arn:aws:bedrock:us-east-1:855673866222:knowledge-base/QQJTQJ1VWU"
            ]
        }
    ]
}

{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "aws-marketplace:ViewSubscriptions",
                "aws-marketplace:Subscribe"
            ],
            "Resource": "*"
        }
    ]
}

{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {
                "Service": [
                    "bedrock.amazonaws.com",
                    "lambda.amazonaws.com"
                ]
            },
            "Action": "sts:AssumeRole"
        }
    ]
}