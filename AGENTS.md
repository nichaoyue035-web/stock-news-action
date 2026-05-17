# AGENTS.md

These rules apply to every task in this project unless explicitly overridden.

Project goal:
This project is an AI-powered information collection and alert assistant.
It collects stock, fund, market, and news data, analyzes useful information, and sends summaries or alerts to Telegram.

Primary priorities:
1. Reliability
2. Clear logging
3. Correct Telegram delivery
4. Minimal changes
5. Avoid false success

## Rule 1 - Understand Before Coding

Before editing code, identify:
- What the user wants
- Which files are involved
- What success means
- What could break

If the task is ambiguous, state the ambiguity first.

## Rule 2 - Simplicity First

Use the minimum code needed to solve the problem.
Do not add unnecessary features.
Do not create abstractions for one-time logic.
Do not rewrite the project unless explicitly requested.

## Rule 3 - Surgical Changes

Only modify files directly related to the task.
Do not reformat unrelated files.
Do not rename functions or restructure modules unless necessary.
Match the existing project style.

## Rule 4 - Reliability First

For data fetching, Telegram sending, and GitHub Actions workflows:
- Add useful error handling
- Avoid silent failures
- Log clear failure reasons
- Do not mark a task as successful if important data was skipped

## Rule 5 - Do Not Hide Failures

If RSS, API, Telegram, GitHub Actions, or AI analysis fails:
- Report the failure clearly
- Keep the rest of the program running when possible
- Do not pretend everything succeeded

"Completed" is wrong if something important was skipped silently.

## Rule 6 - Use AI Only Where It Adds Value

Use the AI model for:
- Summarizing news
- Classifying importance
- Extracting key information
- Drafting Telegram messages
- Reasoning about market context

Do not use the AI model for:
- Simple routing
- Retry logic
- JSON parsing
- Date formatting
- Deterministic transformations

If normal code can solve it, use normal code.

## Rule 7 - Protect Secrets

Never print or expose secrets, including:
- TG_BOT_TOKEN
- TG_CHAT_ID
- TG_BOT_TOKEN_MONITOR
- TG_CHAT_ID_MONITOR
- DEEPSEEK_API_KEY
- GitHub tokens
- API keys

Do not commit real secrets into the repository.
Use environment variables or GitHub Actions secrets.

## Rule 8 - Read Before Writing

Before modifying a function, read:
- The function itself
- Its direct callers
- Its return format
- Related config or environment variables

Do not assume a function is unused without checking.

## Rule 9 - Tests and Verification Matter

After each meaningful change, explain how to verify it.

Prefer verification such as:
- Running the script locally
- Running the relevant GitHub Actions workflow
- Checking Telegram output
- Checking GitHub Actions logs
- Testing failure cases

If tests were not run, say so clearly.

## Rule 10 - Checkpoint After Important Changes

After significant changes, summarize:
- What was changed
- Why it was changed
- Which files were modified
- What was verified
- What still needs attention

## Rule 11 - Match Existing Conventions

Follow the current codebase style.
Do not introduce a new style unless necessary.
If an existing convention is harmful, mention it instead of silently changing everything.

## Rule 12 - No Speculative Market Claims

For stock, fund, and market analysis:
- Do not invent data
- Do not make unsupported claims
- Clearly separate facts, assumptions, and model judgment
- If current market data is unavailable, say so

This project should assist decision-making, not pretend to be certain.
