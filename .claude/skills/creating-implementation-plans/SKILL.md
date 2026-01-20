---
name: creating-implementation-plans
description: Use when explicitly asked to create an implementation plan for a feature or task. Triggers on "create a plan", "plan out", "/plan", or "implementation plan".
---

# Creating Implementation Plans

## Overview

Research the codebase, design an approach, and produce a detailed implementation plan.

**Core principle:** Understand before planning, plan before coding. Ask questions freely.

**Announce at start:** "I'm using the creating-implementation-plans skill to design this implementation."

## When to Use

Use this skill when the user explicitly requests a plan:
- "Create a plan for..."
- "Plan out how to..."
- "/plan <description>"
- "I need an implementation plan for..."

## Asking Questions

**You are encouraged to ask questions at any point:**
- During initial analysis
- While researching the codebase
- When designing the approach
- When breaking down phases and tasks
- When something is unclear or ambiguous

**Don't guess - ask.** The human partner has context you may not have.

## The Process

### Step 1: Initial Analysis (Sequential Thinking)

**If sequential-thinking MCP server is available:**
1. Use `mcp__sequentialthinking__sequentialthinking` to break down the problem
2. Think through the request systematically
3. Identify what you need to understand before proceeding

**If not available:**
1. Break down the request into components
2. List what you need to research
3. Identify initial questions

**Ask clarifying questions now** - don't proceed with ambiguity.

### Step 2: Understand the Request
1. Clarify the goal - what problem are we solving?
2. Identify success criteria - how will we know it's done?
3. Define scope boundaries - what's in/out of scope?
4. **Ask questions** if anything is unclear
5. Don't proceed until the goal is clear and agreed

### Step 3: Explore the Codebase
1. **Find relevant files** - Use Glob/Grep to locate related code
2. **Understand existing patterns** - How does similar functionality work?
3. **Identify touch points** - What files will need changes?
4. **Note dependencies** - What does this interact with?

**Ask questions** about anything unexpected you discover.

Document findings:
- Key files and their purposes
- Existing patterns to follow
- Potential risks or complications

### Step 4: Design the Approach
1. **Consider alternatives** - What are the different ways to solve this?
2. **Evaluate trade-offs** - Complexity, maintainability, performance
3. **Choose an approach** - Document the "why" not just the "what"
4. **Identify unknowns** - What might we discover during implementation?

**Ask for input** on key architectural decisions before finalizing.

### Step 5: Break Down into Phases and Tasks

Structure work as **Phases** containing **Tasks**:

**Phase:** A logical grouping of related work (e.g., "Setup", "Core Implementation", "Testing")

**Task:** A specific, testable unit of work within a phase

**Each task MUST include:**
- Clear description of what to do
- Specific files to modify
- Step-by-step implementation instructions
- **Testing requirements** - how to verify it works

**Phase/Task format:**
```markdown
## Phase N: [Phase Name]
Description of this phase's purpose.

### Task N.1: [Short description]
**Files:** `path/to/file.ts`
**Steps:**
1. Step one with specific detail
2. Step two with specific detail
**Testing:**
- [ ] Test case 1: Expected behavior
- [ ] Test case 2: Expected behavior
- [ ] Manual verification: How to check
```

### Step 6: Present Plan for Approval

**IMPORTANT: Do NOT save the plan file yet.**

Present the complete plan in chat:
1. Overview and success criteria
2. Research findings summary
3. Architecture decisions with rationale
4. All phases and tasks with testing
5. Risks and mitigations

Then ask: **"Does this plan look good? I'll save it to `docs/plans/<name>-implementation-plan.md` once you approve."**

### Step 7: Save Plan (After Approval Only)

**Only after explicit approval**, save to: `docs/plans/<feature-name>-implementation-plan.md`

**Plan structure:**
```markdown
# [Feature Name] Implementation Plan

## Overview
Brief description of what we're building and why.

## Success Criteria
- [ ] Criterion 1
- [ ] Criterion 2

## Research Findings

### Relevant Files
- `path/to/file.ts` - Description of relevance

### Existing Patterns
Description of patterns we'll follow.

### Dependencies
What this feature interacts with.

## Architecture Decisions

### Decision 1: [Title]
**Options considered:**
1. Option A - pros/cons
2. Option B - pros/cons

**Chosen:** Option X because...

## Implementation

### Phase 1: [Phase Name]
Description of this phase.

#### Task 1.1: [Description]
**Files:** `path/to/file.ts`
**Steps:**
1. Specific step
2. Specific step
**Testing:**
- [ ] Test case with expected result
- [ ] Manual verification step

#### Task 1.2: [Description]
...

### Phase 2: [Phase Name]
...

## End-to-End Testing
How to verify the complete feature works.

## Risks and Mitigations
- Risk 1: Mitigation strategy
```

## Plan Quality Checklist

Before presenting for approval, verify:
- [ ] Goal and success criteria are clear
- [ ] Codebase research is documented
- [ ] Architecture decisions explain the "why"
- [ ] Work is organized into logical phases
- [ ] Tasks are small and actionable
- [ ] **Every task has testing requirements**
- [ ] Risks are identified
- [ ] Questions have been asked and answered

## When to Stop and Ask

**STOP and ask when:**
- Requirements are ambiguous
- Multiple valid approaches exist - need preference
- You discover something unexpected
- The scope seems larger than expected
- You're unsure about testing approach
- Any technical decision could go multiple ways

**Don't assume - clarify. Don't guess - ask.**

## Remember
- Use sequential thinking for initial analysis (if available)
- Ask questions freely throughout the process
- Research before designing
- Design before task breakdown
- Organize work into Phases -> Tasks
- **Every task must have testing**
- Present plan in chat FIRST
- Only save file AFTER approval
- Document decisions and rationale
