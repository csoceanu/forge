You are reviewing a pull request description against the commit messages from recent CI fix commits.

> **IMPORTANT**: Return ONLY the PR description content. Do not include any analysis, reasoning, or commentary. Start directly with the PR description.

## Current PR Description

{current_description}

## CI Fix Commit Messages

```
{commit_log}
```

## Instructions

1. Read the commit messages to understand what was changed by the CI fixes.
2. Identify any facts in the PR description that are **directly contradicted** by the commit messages — changed constants, thresholds, percentages, algorithm logic, expected values, or test timeouts.
3. Rewrite only the affected sentences or paragraphs to match what the commits describe. Leave everything else unchanged.
4. Do not add new sections. Do not remove existing sections unless the commits explicitly remove that feature.
5. If the description is accurate as written, return it **unchanged**.
6. Return the **full updated description** — not a diff, not a summary, not an explanation.

Focus on: changed constants, thresholds, percentages, algorithm logic, expected outcomes, test timeouts.
Ignore: lint fixes, codegen updates, import reordering, whitespace changes.
