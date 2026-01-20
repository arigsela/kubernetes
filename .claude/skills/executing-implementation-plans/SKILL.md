---
name: executing-implementation-plans
description: Use when you have an approved implementation plan to execute with phase-based checkpoints. Triggers on "execute the plan", "implement the plan", or referencing a plan file.
---

# Executing Implementation Plans

## Overview

Load an approved plan, execute phases with tasks, run tests, and checkpoint between phases for review.

**Core principle:** Execute methodically, test everything, checkpoint between phases.

**Announce at start:** "I'm using the executing-implementation-plans skill to implement this plan."

## When to Use

Use this skill when:
- An implementation plan exists in `docs/plans/`
- User asks to "execute the plan" or "implement the plan"
- User references a specific plan file to execute

## Asking Questions

**You are encouraged to ask questions at any point:**
- When a task instruction is unclear
- When you encounter unexpected code or behavior
- When a test fails and you're unsure how to proceed
- When you discover something that might affect the plan
- When you need to deviate from the plan

**Don't guess - ask.** Better to pause and clarify than to implement incorrectly.

## The Process

### Step 1: Load and Review Plan

1. Read the plan file from `docs/plans/`
2. Review critically:
   - Do you understand every phase and task?
   - Are there any gaps or ambiguities?
   - Do the testing requirements make sense?
3. **If concerns exist:** Raise them before starting
4. **If no concerns:** Proceed to Step 2

Ask: "I've reviewed the plan. [Any concerns?] Ready to start with Phase 1?"

### Step 2: Create TodoWrite Tracking

Create a todo list tracking all phases and tasks:

```
Phase 1: [Name]
  - Task 1.1: [Description]
  - Task 1.2: [Description]
Phase 2: [Name]
  - Task 2.1: [Description]
  ...
```

Mark the first task as `in_progress`.

### Step 3: Execute Current Phase

For each task in the current phase:

#### 3a. Start Task
1. Mark task as `in_progress` in TodoWrite
2. Announce: "Starting Task X.Y: [description]"

#### 3b. Implement Task
1. Follow each step exactly as written in the plan
2. **Ask questions** if any step is unclear
3. Make the code changes specified

#### 3c. Run Task Tests
1. Execute ALL testing requirements listed for the task
2. Document test results:
   - [ ] Test passed / failed
   - What was the actual result?

#### 3d. Complete Task
- **If all tests pass:** Mark task as `completed`
- **If tests fail:** STOP and report (see "When Tests Fail")

### Step 4: Phase Checkpoint

When all tasks in a phase are complete:

**Report to user:**
```
## Phase X Complete: [Phase Name]

### Tasks Completed:
- Task X.1: [Brief description] ✓
- Task X.2: [Brief description] ✓

### Test Results:
- [Test 1]: Passed ✓
- [Test 2]: Passed ✓

### Notes:
[Any observations, minor deviations, or concerns]

Ready for Phase [X+1]? Or would you like to review anything first?
```

**Wait for approval before continuing to next phase.**

### Step 5: Continue to Next Phase

After approval:
1. Update TodoWrite for next phase
2. Repeat Step 3 for each task
3. Repeat Step 4 at phase end

### Step 6: Final Completion

When all phases are complete:

**Final Report:**
```
## Implementation Complete: [Feature Name]

### All Phases:
- Phase 1: [Name] ✓
- Phase 2: [Name] ✓
- ...

### End-to-End Testing:
[Run the end-to-end tests from the plan]
- Result: [Pass/Fail with details]

### Summary:
[What was built, any deviations from plan, observations]

### Next Steps:
[Suggestions: commit, PR, additional testing, etc.]
```

## When Tests Fail

**STOP immediately and report:**

```
## Test Failure in Task X.Y

**Task:** [Description]
**Test that failed:** [Test description]
**Expected:** [What should happen]
**Actual:** [What happened]

**My analysis:**
[What you think went wrong]

**Options:**
1. [Suggested fix]
2. [Alternative approach]
3. [Need more information]

How would you like to proceed?
```

**Do not continue to next task until resolved.**

## When to Stop and Ask

**STOP executing and ask when:**
- A task instruction is ambiguous
- You encounter unexpected code structure
- A test fails
- You need to deviate from the plan
- Something seems wrong or risky
- You discover a dependency issue
- The plan seems outdated vs. current code

**Don't force through blockers - stop and ask.**

## Handling Plan Deviations

Sometimes you need to deviate from the plan:

**Minor deviations** (different file path, slight refactor):
- Note it in the phase checkpoint report
- Continue executing

**Major deviations** (different approach, new tasks needed):
- STOP and report to user
- Propose the deviation
- Wait for approval before continuing

## TodoWrite Management

Keep the todo list updated in real-time:

- Mark task `in_progress` when starting
- Mark task `completed` immediately when done (don't batch)
- Add new tasks if discovered during execution
- Remove tasks if they become unnecessary (with note to user)

## Execution Quality Checklist

For each task, verify:
- [ ] All steps from plan were followed
- [ ] Code changes match the plan's intent
- [ ] All specified tests were run
- [ ] Test results are documented
- [ ] Any deviations are noted

For each phase checkpoint:
- [ ] All tasks in phase are complete
- [ ] All tests passed
- [ ] User has approved continuing

## Remember

- Review plan critically before starting
- Execute one task at a time
- **Run all tests for each task**
- Ask questions when unclear
- Stop on test failures
- Checkpoint between phases
- Wait for approval before next phase
- Document everything
- Don't guess - ask
